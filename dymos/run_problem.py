import warnings

from .grid_refinement.ph_adaptive.ph_adaptive import PHAdaptive
from .grid_refinement.hp_adaptive.hp_adaptive import HPAdaptive
from .grid_refinement.write_iteration import write_error, write_refine_iter
from .grid_refinement.refinement import _refine_iter
from .phase.phase import Phase

import openmdao.api as om
import dymos as dm
import numpy as np
from dymos.trajectory.trajectory import Trajectory
from dymos.load_case import load_case, find_phases
from dymos.grid_refinement.error_estimation import check_error
import os
import sys


def modify_problem(problem, restart=None, reset_grid=False):
    """
    Modifies the problem object by loading in a guess from a specified restart file.
    Parameters
    ----------
    problem : om.Problem
        The problem instance being modified.
    restart : String or None
        The name of a database to use for restarting the problem.
    reset_grid: Boolean
        Flag to trigger a grid reset.
    """
    # record variables to database when running driver under hook
    # pre-hook is important, because recording initialization is skipped if final_setup has run once
    save_db = os.getcwd() + '/dymos_solution.db'

    try:
        os.remove(save_db)
    except FileNotFoundError:
        pass  # OK if old database is not present to be deleted

    print('adding recorder at:', save_db)
    problem.add_recorder(om.SqliteRecorder(save_db))
    problem.recording_options['includes'] = ['*']
    problem.recording_options['record_inputs'] = True

    # if opts.get('reset_grid'):  # TODO: implement this option
    #     pass

    if restart is not None:  # restore variables from database file specified by 'restart'
        print('Restarting run_problem using the %s database.' % restart)
        cr = om.CaseReader(restart)

        # find the proper case
        try:
            case = cr.get_case('final')
        except RuntimeError:
            cases = cr.list_cases()
            if len(cases) < 1:
                print('WARNING: the requested %s database file does not have any cases to load.' % restart)
                return
            case = cr.get_case(cases[-1])  # use last case, ideally it should be the only one

        check_simulation = cr.problem_metadata['driver']['name'] == 'Driver'
        if check_simulation:
            prev_soln = {'inputs':  case.list_inputs(out_stream=None,  units=True, prom_name=True),
                         'outputs': case.list_outputs(out_stream=None, units=True, prom_name=True)}

            load_case(problem, prev_soln)
        else:
            # Initialize the system with values from the case.
            # We unnecessarily call setup again just to make sure we obliterate the previous solution
            # First reset the connections at the top level model until fixed in OpenMDAO
            problem.setup()

            # Load the values from the previous solution
            load_case(problem, case)


def run_problem(problem, refine_method='hp', refine_iteration_limit=0, run_driver=True,
                simulate=False, restart=None):
    """
    A Dymos-specific interface to execute an OpenMDAO problem containing Dymos Trajectories or
    Phases.  This function can iteratively call run_driver to perform grid refinement, and automatically
    call simulate following a run to check the validity of a result.

    Parameters
    ----------
    problem : om.Problem
        The OpenMDAO problem object to be run.
    refine : bool
        If True, perform grid refinement on the Phases found in the Problem.
    refine_method : String
        The choice of refinement algorithm to use for grid refinement
    refine_iteration_limit : int
        The number of passes through the grid refinement algorithm to be made.
    run_driver : bool
        If True, run the driver (optimize the problem), otherwise just run the model one time.
    no_iterate : bool
        If True, run the driver but do not iterate.
    simulate : bool
        If True, perform a simulation of Trajectories found in the Problem after the driver
        has been run and grid refinement is complete.
    """
    problem.final_setup()  # make sure command line option hook has a chance to run

    if restart is not None:
        cr = om.CaseReader(restart)
        system_cases = cr.list_cases('root')
        case = cr.get_case(system_cases[-1])
        load_case(problem, case)

    if run_driver:
        problem.run_driver()
        _refine_iter(problem, refine_iteration_limit, refine_method)
    else:
        problem.run_model()
        if refine_iteration_limit > 0:
            warnings.warn("Refinement not performed. Set run_driver to True to perform refinement.")

    problem.record('final')  # save case for potential restart

    if simulate:
        for subsys in problem.model.system_iter(include_self=True, recurse=True):
            if isinstance(subsys, Trajectory):
                subsys.simulate(record_file='dymos_simulation.db')
