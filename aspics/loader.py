import os
import numpy as np
from yaml import load, SafeLoader

from aspics.simulator import Simulator
from aspics.snapshot import Snapshot
from aspics.params import Params, IndividualHazardMultipliers, LocationHazardMultipliers


def setup_sim_from_file(parameters_file):
    print(f"Running a simulation based on {parameters_file}")
    with open(parameters_file, "r") as f:
        parameters = load(f, Loader=SafeLoader)
        return setup_sim(parameters)


def setup_sim(parameters):
    print(f"Running a manually added parameters simulation based on {parameters}")

    sim_params = parameters["microsim"] ## Set of Parameters for the ASPCIS microsim
    calibration_params = parameters["microsim_calibration"] ## Calibration paramaters
    disease_params = parameters["disease"]
    health_conditions = parameters["health_conditions"]
    iterations = sim_params["iterations"]
    study_area = sim_params["study-area"]
    output = sim_params["output"]
    output_every_iteration = sim_params["output-every-iteration"]
    use_lockdown = sim_params["use-lockdown"]
    start_date = sim_params["start-date"]

    # Check the parameters are sensible
    if iterations < 1:
        raise ValueError("Iterations must be > 1")
    if (not output) and output_every_iteration:
        raise ValueError(
            "Can't choose to not output any data (output=False) but also write the data at every "
            "iteration (output_every_iteration=True)"
        )

    # Load the snapshot file
    snapshot_path = f"data/snapshots/{study_area}/cache.npz"
    if not os.path.exists(snapshot_path):
        raise Exception(
            f"Missing snapshot cache {snapshot_path}. Run SPC and convert_snapshot.py first to generate it."
        )
    print(f"Loading snapshot from {snapshot_path}")
    snapshot = Snapshot.load_full_snapshot(path=snapshot_path)
    print(f"Snapshot is {int(snapshot.num_bytes() / 1000000)} MB")

    # Apply lockdown values
    if use_lockdown:
        # Skip past the first entries
        snapshot.lockdown_multipliers = snapshot.lockdown_multipliers[start_date:]
    else:
        # No lockdown
        snapshot.lockdown_multipliers = np.ones(iterations + 1)

    # TODO set the random seed of the model. Why do we still have this very random number
    snapshot.seed_prngs(42)

    # set params
    if calibration_params is not None and disease_params is not None:
        snapshot.update_params(create_params(calibration_params, disease_params, health_conditions))
        health_type = health_conditions["type"]
        if health_type["improve_health"]:
            print("Switching to healthier population")
            snapshot.switch_to_healthier_population()

    # Add the new function to compute individual risks
    # new_params = NewParamsters( Old_paramaters_that_changed()) # The function


    # Create a simulator and upload the snapshot data to the OpenCL device
    simulator = Simulator(snapshot, study_area, gpu=True)
    [people_statuses, people_transition_times] = simulator.seeding_base()
    simulator.upload_all(snapshot.buffers)
    simulator.upload("people_statuses", people_statuses)
    simulator.upload("people_transition_times", people_transition_times)

    return simulator, snapshot, study_area, iterations


def create_params(calibration_params, disease_params, health_conditions):

    current_risk_beta = disease_params["current_risk_beta"]
    # NB: OpenCL model incorporates the current risk beta by pre-multiplying the hazard multipliers with it
    location_hazard_multipliers = LocationHazardMultipliers(
        retail=calibration_params["hazard_location_multipliers"]["Retail"]
               * current_risk_beta,
        primary_school=calibration_params["hazard_location_multipliers"][
                           "PrimarySchool"
                       ]
                       * current_risk_beta,
        secondary_school=calibration_params["hazard_location_multipliers"][
                             "SecondarySchool"
                         ]
                         * current_risk_beta,
        home=calibration_params["hazard_location_multipliers"]["Home"]
             * current_risk_beta,
        work=calibration_params["hazard_location_multipliers"]["Work"]
             * current_risk_beta,
    )

    individual_hazard_multipliers = IndividualHazardMultipliers(
        presymptomatic=calibration_params["hazard_individual_multipliers"][
            "presymptomatic"
        ],
        asymptomatic=calibration_params["hazard_individual_multipliers"][
            "asymptomatic"
        ],
        symptomatic=calibration_params["hazard_individual_multipliers"]["symptomatic"],
    )
    BMI_params = health_conditions["BMI"]
    health_type_params = health_conditions["type"]
    bmi_multipliers = BMI_params[
        BMI_params["white_Ethni_coff1"],
        BMI_params["white_Ethni_coff2"],
        BMI_params["white_Ethni_coff3"],
        BMI_params["black_Ethni_coff1"],
        BMI_params["black_Ethni_coff2"],
        BMI_params["black_Ethni_coff3"],
        BMI_params["asian_Ethni_coff1"],
        BMI_params["asian_Ethni_coff2"],
        BMI_params["asian_Ethni_coff3"],
        BMI_params["other_Ethni_coff1"],
        BMI_params["other_Ethni_coff2"],
        BMI_params["other_Ethni_coff3"],
    ]
    health_type = health_conditions["type"]
    obesity_multipliers = [
        health_type_params["global_bmi"],
        health_type_params["overweight"],
        health_type_params["obesity_30"],
        health_type_params["obesity_35"],
        health_type_params["obesity_40"],
    ]

    return Params(
        location_hazard_multipliers=location_hazard_multipliers,
        individual_hazard_multipliers=individual_hazard_multipliers,
        obesity_multipliers=obesity_multipliers,
        cvd_multiplier=health_type["cvd"],
        diabetes_multiplier=health_type["diabetes"],
        bloodpressure_multiplier=health_type["bloodpressure"],
    )
