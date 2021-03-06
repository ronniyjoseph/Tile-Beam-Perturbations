import numpy
import sys
import time

from scipy.constants import c
from scipy import interpolate

from functools import partial
import multiprocessing

import powerbox

from generaltools import from_lm_to_theta_phi
from skymodel import SkyRealisation

from radiotelescope import RadioTelescope
from radiotelescope import ideal_gaussian_beam
from radiotelescope import broken_gaussian_beam
from radiotelescope import ideal_mwa_beam_loader
from radiotelescope import broken_mwa_beam_loader

from powerspectrum import get_power_spectrum
import os
import argparse


def main(beam_type = 'gaussian', faulty_dipole = 1, faulty_tile = 36, n_channels = 100, calibrate = True, verbose=True):
    print(beam_type, faulty_dipole, faulty_dipole, n_channels, calibrate, verbose)
    output_path = "/data/rjoseph/Hybrid_Calibration/Tile_Pertubation/Simulation_Output/"
    prefix = "TEST"
    suffix = ""

    path = "Data/MWA_All_Coordinates_Cath.txt"
    frequency_range = numpy.linspace(135, 165, n_channels) * 1e6
    #faulty_dipole = 1 #6
    #faulty_tile = 36 #1036, 81, 36
    sky_param = "random"
    mode = "parallel"
    processes = 2
    #calibrate = True
    #beam_type = "gaussian"
    plot_file_name = "Compare_MWA_Beam_Core_Gain_Corrected_1.pdf"

    telescope = RadioTelescope(load = True, path=path, verbose = verbose)
    baseline_table = telescope.baseline_table
    source_population = SkyRealisation(sky_type=sky_param, verbose = verbose)

    ####################################################################################################################
    if verbose:
        print("Generating visibility measurements for each frequency")
    ideal_measured_visibilities, broken_measured_visibilities = get_observations(source_population, baseline_table,
                                                                                    faulty_dipole, faulty_tile,
                                                                                   frequency_range, beam_type, calibrate,
                                                                                   compute_mode = mode, processes = processes)
    #############################################################################################################################

    #save simulated data:
    project_name = prefix + beam_type + "_tile" + str(faulty_tile) +"_dipole" + str(faulty_dipole) + "_corrected_" \
                   + str(calibrate) + suffix

    if not os.path.exists(output_path + project_name):
        print
        ""
        print
        "!!!Warning: Creating output folder at output destination!"
        os.makedirs(output_path + project_name)
        output_types = ["ideal", "broken"]
        numpy.save(output_path + project_name + "/" + "ideal" + "_simulated_data", ideal_measured_visibilities)
        numpy.save(output_path + project_name + "/" + "broken" + "_simulated_data", broken_measured_visibilities)

    file = open(output_path + project_name + "/" + "simulation_parameters.log", "w")
    file.write(f"Frequency range: {numpy.min(frequency_range)} - {numpy.max(frequency_range)} MHz \n")
    file.write(f"Faulty Dipole: {faulty_dipole}\n")
    file.write(f"Faulty Tile: {faulty_tile}\n")
    file.write(f"Sky Parameters: {sky_param} \n")
    file.write(f"Calibrate: {calibrate}\n")
    file.write(f"Beam model:{beam_type} \n")
    file.write(f"Position File: {path}")
    file.close()

    get_power_spectrum(frequency_range, telescope, ideal_measured_visibilities, broken_measured_visibilities,
                    faulty_tile, output_path + project_name + "/" + plot_file_name, verbose)

    return





def get_observations(source_population, baseline_table, faulty_dipole, faulty_tile, frequency_range, beam_type,
                     calibrate, compute_mode = 'serial', processes= None):
    print(f"Running calculations in {compute_mode}")

    if compute_mode == "parallel":
        ideal_observations, broken_observations = get_observation_MP(source_population, baseline_table, faulty_dipole,
                                                                     faulty_tile, frequency_range, beam_type,
                                                                     calibrate, processes= processes)
    elif compute_mode == "serial":
        ideal_observations, broken_observations = get_observations_serial(source_population, baseline_table,
                                                                         faulty_dipole, faulty_tile, frequency_range,
                                                                         beam_type, calibrate)
    elif compute_mode == "high_memory":
        ideal_observations, broken_observations = get_observations_memory(source_population, baseline_table, frequency_range,
                                                     beam_type = beam_type , calibrate = calibrate, faulty_dipole =faulty_dipole
                                                                          ,faulty_tile =faulty_tile)

    else:
        raise ValueError(f"compute_mode can be 'parallel', 'serial', or 'high_memory' NOT {compute_mode}")

    return ideal_observations, broken_observations


def get_observation_MP(source_population, baseline_table, faulty_dipole, faulty_tile, frequency_range, beam_type,
                       calibrate, processes = 4):
    #Determine maximum resolution
    max_frequency = frequency_range[-1]
    max_u = numpy.max(numpy.abs(baseline_table.u(max_frequency)))
    max_v = numpy.max(numpy.abs(baseline_table.v(max_frequency)))
    max_b = max(max_u, max_v)
    # sky_resolutions
    min_l = 1. / (2*max_b)

    pool = multiprocessing.Pool(processes=processes)
    iterator = partial(get_observation_single_channel, source_population, baseline_table, min_l, faulty_dipole,
                       faulty_tile, beam_type, frequency_range, calibrate )
    ideal_observations_list, broken_observations_list = zip(*pool.map(iterator, range(len(frequency_range))))

    ideal_observations = numpy.moveaxis(numpy.array(ideal_observations_list), 0, -1)
    broken_observations = numpy.moveaxis(numpy.array(broken_observations_list), 0, -1)

    return ideal_observations, broken_observations


def get_observations_serial(source_population, baseline_table, faulty_dipole, faulty_tile, frequency_range, beam_type,
                       calibrate):
    #Determine maximum resolution
    max_frequency = frequency_range[-1]
    max_u = numpy.max(numpy.abs(baseline_table.u(max_frequency)))
    max_v = numpy.max(numpy.abs(baseline_table.v(max_frequency)))
    max_b = max(max_u, max_v)
    # sky_resolutions
    min_l = 1. / (2*max_b)

    ideal_observations = numpy.zeros((baseline_table.number_of_baselines, len(frequency_range)), dtype=complex)
    broken_observations = ideal_observations.copy()

    for frequency_index in range(len(frequency_range)):
        ideal_observations[..., frequency_index], broken_observations[..., frequency_index]  = \
        get_observation_single_channel(source_population, baseline_table, min_l, faulty_dipole, faulty_tile, beam_type,
                                       frequency_range, calibrate, frequency_index)

    return ideal_observations, broken_observations

def get_observation_single_channel(source_population, baseline_table, min_l, faulty_dipole, faulty_tile, beam_type,
                                   frequency_range, calibrate, frequency_index):
    sky_image, l_coordinates = source_population.create_sky_image(
        frequency_channels=frequency_range[frequency_index], resolution=min_l, oversampling=1)
    ll, mm = numpy.meshgrid(l_coordinates, l_coordinates)

    # Create Beam
    #############################################################################
    if beam_type == "MWA":
        tt, pp, = from_lm_to_theta_phi(ll, mm)
        ideal_beam = ideal_mwa_beam_loader(tt, pp, frequency_range[frequency_index], load= False)
        broken_beam = broken_mwa_beam_loader(tt, pp, frequency_range[frequency_index], faulty_dipole, load= False)

    elif beam_type == "gaussian":
        ideal_beam = ideal_gaussian_beam(ll, mm, frequency_range[frequency_index])
        broken_beam = broken_gaussian_beam(ll, mm, frequency_range[frequency_index], faulty_dipole)
    else:
        raise ValueError("The only valid option for the beam are 'MWA' or 'gaussian'")

    if calibrate:
        correction = 16/15
    else:
        correction = 1

    ##Determine the indices of the broken baselines and calulcate the visibility measurements
    ##################################################################

    broken_baseline_indices = numpy.where((baseline_table.antenna_id1 == faulty_tile) |
                                          (baseline_table.antenna_id2 == faulty_tile))[0]

    ideal_measured_visibilities = visibility_extractor(baseline_table, sky_image, frequency_range[frequency_index],
                                                                           ideal_beam, ideal_beam)

    broken_measured_visibilities = ideal_measured_visibilities.copy()
    broken_measured_visibilities[broken_baseline_indices] = visibility_extractor(
        baseline_table.sub_table(broken_baseline_indices), sky_image, frequency_range[frequency_index],
        ideal_beam, broken_beam)*correction

    return ideal_measured_visibilities, broken_measured_visibilities


def get_observations_memory(source_population = None, baseline_table = None, frequency_range = None, faulty_dipole=None,
                            faulty_tile = None, beam_type = 'gaussian', calibrate=False, oversampling=2,
                            padding_factor = 1):

    print("Computing idealised MWA observations")
    ideal_observations = get_obsersvations_all_channels(source_population = source_population,
                                                        baseline_table = baseline_table,
                                                        frequency_range = frequency_range,
                                                        faulty_dipole=None, faulty_tile = None, beam_type = beam_type,
                                                        calibrate= calibrate, oversampling=oversampling,
                                                        padding_factor = padding_factor)

    broken_baseline_indices = numpy.where((baseline_table.antenna_id1 == faulty_tile) |
                                          (baseline_table.antenna_id2 == faulty_tile))[0]
    broken_observations = ideal_observations.copy()

    if len(broken_baseline_indices) > 0 :
        print("Computing broken MWA observations")
        broken_baseline_table = baseline_table.sub_table(broken_baseline_indices)
        broken_observations[broken_baseline_indices, ...] = get_obsersvations_all_channels(source_population = source_population,
                                                        baseline_table = broken_baseline_table,
                                                        frequency_range = frequency_range,
                                                        faulty_dipole=faulty_dipole, faulty_tile = faulty_tile,
                                                        beam_type = beam_type,
                                                        calibrate= calibrate, oversampling=oversampling,
                                                        padding_factor = padding_factor)

    return ideal_observations, broken_observations


def get_obsersvations_all_channels(source_population = None, baseline_table = None, frequency_range = None, faulty_dipole=None,
                            faulty_tile = None, beam_type = 'gaussian', calibrate=False, oversampling=2):

    #Determine maximum resolution
    max_frequency = frequency_range[-1]
    max_u = numpy.max(numpy.abs(baseline_table.u(max_frequency)))
    max_v = numpy.max(numpy.abs(baseline_table.v(max_frequency)))
    max_b = max(max_u, max_v)
    # sky_resolutions
    min_l = 1. / (2*max_b)*1/oversampling

    sky_cube, l_coordinates = source_population.create_sky_image(resolution=min_l,
                                                                 frequency_channels=frequency_range,
                                                                 oversampling=1)
    ll, mm, ff = numpy.meshgrid(l_coordinates, l_coordinates, frequency_range)

    # Create Beam
    #############################################################################
    if beam_type == "MWA":
        tt, pp, = from_lm_to_theta_phi(ll, mm)
        antenna_response1 = ideal_mwa_beam_loader(tt, pp, ff)
        if faulty_dipole is None:
            antenna_response2 = antenna_response1.copy()
        else:
            antenna_response2 = broken_mwa_beam_loader(tt, pp, ff, faulty_dipole)
    elif beam_type == "gaussian":
        antenna_response1 = ideal_gaussian_beam(ll, mm, ff)
        if faulty_dipole is None:
            antenna_response2 = antenna_response1.copy()
        else:
            antenna_response2 = broken_gaussian_beam(ll, mm, ff, faulty_dipole)
    else:
        raise ValueError("The only valid option for the beam are 'MWA' or 'gaussian'")

    if calibrate:
        correction = 16 / 15
    else:
        correction = 1

    ##Determine the indices of the broken baselines and calculcate the visibility measurements
    ##################################################################

    #apparent_sky = sky_cube * antenna_response1 * numpy.conj(antenna_response2)
    #pad_size = padding_factor * apparent_sky.shape[0]

    #padded_shifted_sky = numpy.fft.ifftshift(numpy.pad(apparent_sky, ((pad_size, pad_size), (pad_size, pad_size),
    #                                                                  (0, 0)), mode="constant"), axes=(0, 1))
    #visibility_grid, uv_coordinates = powerbox.dft.fft(padded_shifted_sky, L=2 * (2 * padding_factor + 1), axes=(0, 1))

    observations = numpy.zeros((baseline_table.number_of_baselines, len(frequency_range)), dtype=complex)

    for frequency_index in range(len(frequency_range)):
        observations[..., frequency_index] = visibility_extractor(baseline_table, sky_cube, frequency_range,
                                                                  antenna_response1, antenna_response2)

    return observations*correction



def visibility_extractor(baseline_table_object, sky_image, frequency, antenna1_response,
                            antenna2_response, padding_factor = 3):

    apparent_sky = sky_image * antenna1_response * numpy.conj(antenna2_response)

    padded_sky = numpy.pad(apparent_sky, padding_factor * apparent_sky.shape[0], mode="constant")
    shifted_image = numpy.fft.ifftshift(padded_sky, axes=(0, 1))
    visibility_grid, uv_coordinates = powerbox.dft.fft(shifted_image, L=2*(2 * padding_factor + 1), axes=(0, 1))


    measured_visibilities = uv_list_to_baseline_measurements(baseline_table_object, frequency, visibility_grid,
                                                             uv_coordinates)

    return measured_visibilities

def uv_list_to_baseline_measurements(baseline_table_object, frequency, visibility_grid, uv_grid):

    u_bin_centers = uv_grid[0]
    v_bin_centers = uv_grid[1]

    baseline_coordinates = numpy.array([baseline_table_object.u(frequency), baseline_table_object.v(frequency)])
    # now we have the bin edges we can start binning our baseline table
    # Create an empty array to store our baseline measurements in
    visibility_data = visibility_grid

    real_component = interpolate.RegularGridInterpolator([u_bin_centers, v_bin_centers], numpy.real(visibility_data))
    imag_component = interpolate.RegularGridInterpolator([u_bin_centers, v_bin_centers], numpy.imag(visibility_data))

    visibilities = real_component(baseline_coordinates.T) + 1j*imag_component(baseline_coordinates.T)

    return visibilities


if __name__ == "__main__":
    start = time.process_time()
    parser = argparse.ArgumentParser(description='Broken Tile Simulation Set Up')
    parser.add_argument('-beam', action='store', default="gaussian", type=str)
    parser.add_argument('-broken_dipole', action='store', default = 1, type =int)
    parser.add_argument('-broken_tile', action='store', default= 1, type = int )
    parser.add_argument('-number_channels', action='store', default = 100, type=int)
    parser.add_argument('-calibrate', action='store_true', default=True)
    parser.add_argument('-verbose', action = 'store_true', default = True)
    args = parser.parse_args()
    main(args.beam, args.broken_dipole, args.broken_tile, args.number_channels, args.calibrate, args.verbose)
    end = time.process_time()
    print("Total time is", end - start)
