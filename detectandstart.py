#!/usr/bin/python3

import logging.config
import os
import subprocess
import sys
import pyudev
from libs.Camera import *
from libs.Updater import Updater
from libs.Uploader import Uploader, GenericUploader
from libs.Light import ThreadedLights
from libs.Sensor import ThreadedSenseHat, ThreadedDHT
from threading import Lock

__author__ = "Gareth Dunstone"
__copyright__ = "Copyright 2016, Borevitz Lab"
__credits__ = ["Gareth Dunstone", "Tim Brown", "Justin Borevitz", "Kevin Murray", "Jack Adamson"]
__license__ = "GPL"
__version__ = "3.2.8"
__maintainer__ = "Gareth Dunstone"
__email__ = "gareth.dunstone@anu.edu.au"
__status__ = "Feature rollout"

# attempt to setup logging.
try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass


def detect_picam(updater: Updater) -> tuple:
    """
    Detects the existence of a picam
    on all SPC-OS devices this will return true if the picam is installed
    on other rpis it may return false if the raspberrypi-firmware-tools is not installed or the boot.cfg flag
    for the camera is not set.

    todo: this shoud return an empty tuple if an ivport is detected.
    todo: clean this up so that it doesnt require subprocess.

    :creates: :mod:`libs.Camera.PiCamera`, :mod:`libs.Uploader.Uploader`
    :param updater: instance that has a `communication_queue` member that implements an `append` method
    :type updater: Updater
    :return: tuple of raspberry pi camera thread and uploader.
    :rtype: tuple(PiCamera, Uploader)
    """
    logger.info("Detecting picamera")
    if not (os.path.exists("/opt/vc/bin/vcgencmd")):
        logger.error("vcgencmd not found, cannot detect picamera.")
        return tuple()
    try:
        cmdret = subprocess.check_output("/opt/vc/bin/vcgencmd get_camera", shell=True).decode()
        if "detected=1" in cmdret:
            camera = ThreadedPiCamera(SysUtil.default_identifier(prefix="picam"), queue=updater.communication_queue)
            updater.add_to_identifiers(camera.identifier)
            workers = (camera, Uploader(SysUtil.default_identifier(prefix="picam"), queue=updater.communication_queue))
            return start_workers(workers)
        else:
            return tuple()
    except subprocess.CalledProcessError as e:
        logger.error("Couldn't detect picamera. Error calling vcgencmd. {}".format(str(e)))
        pass
    except Exception as e:
        logger.error("General Exception in picamera detection. {}".format(str(e)))
    return tuple()


def detect_gphoto(updater: Updater):
    """
    Detects DSLRs using `borevitzlab/gphoto2-cffi <https://github.com/borevitzlab/gphoto2-cffi>`_.

    :creates: :mod:`libs.Camera.GPCamera`, :mod:`libs.Uploader.Uploader`
    :param updater: instance that has a `communication_queue` member that implements an `append` method
    :type updater: Updater
    :return: tuple of camera thread objects and associated uploader thread objects.
    :rtype: tuple(GPCamera, Uploader)
    """
    try:
        logger.info("Detecting DSLRs")
        lock = Lock()
        with lock:
            cameras = gp.list_cameras()
            info = [(c._usb_address, c.status.serialnumber) for c in cameras]
        workers = []
        logger.debug("List of cameras is {} long".format(str(len(info))))
        for usb_add, serialnumber in info:
            try:
                identifier = SysUtil.default_identifier(prefix=serialnumber)
                camera = ThreadedGPCamera(identifier=identifier,
                                          lock=lock,
                                          queue=updater.communication_queue)
                updater.add_to_temp_identifiers(camera.identifier)
                uploader = Uploader(camera.identifier, queue=updater.communication_queue)
                workers.extend([camera, uploader])
                logger.debug("Sucessfully detected {} @ {}".format(serialnumber, ":".join(str(si) for si in usb_add)))
            except Exception as ef:
                logger.error("Couldnt detect camera {}".format(str(ef)))
        return start_workers(tuple(workers))
    except Exception as e:
        logger.error("Detecting gphoto cameras failed {}".format(str(e)))
    return tuple()


def detect_webcam(updater: Updater) -> tuple:
    """
    Detects usb web camers using the video4linux pyudev subsystem.

    i.e. if the camera shows up as a /dev/videoX device, it sould be detected here.

    :creates: :mod:`libs.Camera.USBCamera`, :mod:`libs.Uploader.Uploader`
    :param updater: instance that has a `communication_queue` member that implements an `append` method
    :type updater: Updater
    :return: tuple of camera thread objects and associated uploader thread objects.
    :rtype: tuple(USBCamera, Uploader)
    """
    try:
        logger.info("Detecting USB web cameras.")
        workers = []
        for device in pyudev.Context().list_devices(subsystem="video4linux"):
            serial = device.get("ID_SERIAL_SHORT", None)
            if not serial:
                serial = device.get("ID_SERIAL", None)
                if len(serial) > 6:
                    serial = serial[:6]
                logger.info("Detected USB camera. Using default machine id serial {}".format(str(serial)))
            else:
                logger.info("Detected USB camera {}".format(str(serial)))

            identifier = SysUtil.default_identifier(prefix="USB-{}".format(serial))
            sys_number = device.sys_number

            try:
                # logger.warning("adding {} on {}".format(identifier, sys_number))
                camera = ThreadedUSBCamera(identifier=identifier,
                                           sys_number=sys_number,
                                           queue=updater.communication_queue)
                updater.add_to_temp_identifiers(camera.identifier)
                workers.append(camera)
                workers.append(Uploader(identifier, queue=updater.communication_queue))
            except Exception as e:
                logger.error("Unable to start usb webcamera {} on {}".format(identifier, sys_number))
                logger.error("{}".format(str(e)))
        return start_workers(tuple(workers))
    except Exception as e:
        logger.error("couldnt detect the usb cameras {}".format(str(e)))
    return tuple()


def detect_sensors(updater: Updater) -> tuple:
    """
    Detects sensors from sensor_list file. This is stupid, hacky and awful.

    TODO: make this better and not permanently linked to sftp.traitcapture.org

    :creates: :mod:`libs.Sensor.Sensor`, :mod:`libs.Uploader.Uploader`
    :param updater: instance that has a `communication_queue` member that implements an `append` method
    :type updater: Updater
    :return: tuple of started sensor objects and uploaders for their data
    :rtype: tuple(Sensor, Uploader)
    """
    workers = list()
    try:
        sensors_list = tuple()
        with open("sensor_list") as f:
            sensors_list = f.readlines()

        for s in sensors_list:
            try:
                i = s.lower().strip()
                if i == "sensehat":
                    shat = ThreadedSenseHat(identifier=SysUtil.get_hostname() + "-sensehat",
                                            queue=updater.communication_queue)
                    ul = GenericUploader(shat.identifier, shat.data_directory, "sftp.traitcapture.org")
                    ul.remove_source_files = False
                    workers.append(shat)
                    workers.append(ul)
                else:
                    shat = ThreadedDHT(identifier=SysUtil.get_hostname() + "-" + i,
                                       queue=updater.communication_queue)
                    ul = GenericUploader(shat.identifier, shat.data_directory, "sftp.traitcapture.org")
                    ul.remove_source_files = False
                    workers.append(shat)
                    workers.append(ul)
            except Exception as exc:
                logger.error("Couldnt detect a sensor: {}".format(str(exc)))
        return start_workers(tuple(workers))
    except Exception as e:
        logger.error("Couldnt detect sensors for some reason: {}".format(str(e)))
    return tuple()


def detect_lights(updater: Updater) -> tuple:
    """
    Detects lights from the config files that exist.

    :creates: :mod:`libs.Light.Light`
    :param updater: instance that has a `communication_queue` member that implements an `append` method
    :type updater: Updater
    :return: tuple of light thread objects.
    :rtype: tuple(Light)
    """
    try:
        workers = list()
        for identifier, cfg_p in SysUtil.get_light_configs().items():
            try:
                workers.append(ThreadedLights(identifier, queue=updater.communication_queue))
            except Exception as e:
                logger.error("Couldnt detect light at {} : ".format(identifier, str(e)))
        return start_workers(tuple(workers))
    except Exception as e:
        logger.error("Couldnt detect light configs: {}".format(str(e)))


def detect_ivport(updater: Updater) -> tuple:
    """
    Method to detect IVport multiplexer.
    Its difficult to actually detect the existence of an ivport so we must just assume taht it exists if there is a
    config file matching the right pattern

    :creates: :mod:`libs.Camera.IVPortCamera`, :mod:`libs.Uploader.Uploader`
    :param updater: instance that has a `communication_queue` member that implements an `append` method
    :type updater: Updater
    :return: tuple of camera thread objects and associated uploader thread objects.
    :rtype: tuple(Camera, Uploader)
    """
    from glob import glob
    workers = []
    for iv_conf in list(glob("configs/ivport*.ini")):
        camera = ThreadedIVPortCamera(SysUtil.default_identifier(prefix="ivport"), queue=updater.communication_queue)
        updater.add_to_identifiers(camera.identifier)
        workers.append(camera)
        workers.append(Uploader(SysUtil.default_identifier(prefix="ivport"), queue=updater.communication_queue))
    return start_workers(workers)


def enumerate_usb_devices() -> set:
    """
    Gets a set of the current usb devices from pyudev

    :return: set of pyudev usb device objects
    :rtype: set(pyudev.Device)
    """
    return set(pyudev.Context().list_devices(subsystem="usb"))


def start_workers(worker_objects: tuple or list) -> tuple:
    """
    Starts threaded workers

    :param worker_objects: tuple of worker objects (threads)
    :return: tuple of started worker objects
    :rtype: tuple(threading.Thread)
    """
    logger.debug("Starting {} worker threads".format(str(len(worker_objects))))
    for thread in worker_objects:
        thread.daemon = True
        thread.start()
    return worker_objects


def kill_workers(worker_objects: tuple):
    """
    stops all workers

    calls the stop method of the workers (they should all implement this as they are threads).

    :param worker_objects:
    :type worker_objects: tuple(threading.Thread)
    """
    logger.debug("Killing {} worker threads".format(str(len(worker_objects))))
    for thread in worker_objects:
        thread.stop()


if __name__ == "__main__":

    logger = logging.getLogger("Worker_dispatch")
    logger.info("Program startup...")
    # The main loop for capture

    # these should be all detected at some point.
    gphoto_workers = tuple()
    raspberry = tuple()
    webcams = tuple()
    lights = tuple()
    sensors = tuple()
    updater = None
    try:
        # start the updater. this is the first thing that should happen.
        logger.debug("Starting up the updater")
        updater = Updater()
        start_workers((updater,))

        ivport = detect_ivport(updater)
        raspberry = detect_ivport(updater) or detect_picam(updater)
        webcams = detect_webcam(updater)
        lights = detect_lights(updater)
        sensors = detect_sensors(updater)
        # try 10 times to detect gphoto cameras. Maybe they arent awake yet.
        for x in range(10):
            gphoto_workers = detect_gphoto(updater)
            if gphoto_workers:
                break
            time.sleep(1)
        else:
            logger.warning("no gphoto cameras detected. Something might be wrong.")

        # enumerate the usb devices to compare them later on.
        usb_devices = enumerate_usb_devices()

        while True:
            try:
                if usb_devices != enumerate_usb_devices():
                    logger.warning("USB device list change. Killing camera threads")
                    kill_workers(gphoto_workers)
                    updater.temp_identifiers = set()
                    gphoto_workers = detect_gphoto(updater)
                    # should redetect webcams.

                    webcams = detect_webcam(updater)
                    usb_devices = enumerate_usb_devices()
                time.sleep(1)
            except (KeyboardInterrupt, SystemExit) as e:
                kill_workers(gphoto_workers)
                kill_workers(webcams)
                kill_workers(raspberry)
                kill_workers(lights)
                kill_workers([updater])
                raise e
            except Exception as e:
                logger.fatal("EMERGENCY! Other exception encountered. {}".format(str(e)))

    except (KeyboardInterrupt, SystemExit):
        print("exiting...")
        kill_workers(gphoto_workers)
        kill_workers(raspberry)
        kill_workers(webcams)
        kill_workers(lights)
        kill_workers([updater])
        sys.exit()
    except Exception as e:
        logger.fatal("EMERGENCY! An exception occurred during worker dispatch: {}".format(str(e)))

"""
IVPORt __main__ for when the IVport is in use, as we cant detect its presence yet.
"""
#
# if __name__ == "__main__":
#     logger = logging.getLogger("Worker_dispatch")
#     logger.info("Program startup")
#     # The main loop for capture
#     ivport = list()
#     updater = None
#     try:
#         # start the updater. this is the first thing that should happen.
#         updater = Updater()
#         updater.start()
#
#         ivport = (ThreadedIVPortCamera(SysUtil.default_identifier(prefix="IVPort"), updater),
#                   Uploader(SysUtil.default_identifier(prefix="IVPort"), queue=updater))
#         start_workers(ivport)
#
#     except (KeyboardInterrupt, SystemExit):
#         print("exiting...")
#         kill_workers(ivport)
#         kill_workers([updater])
#         sys.exit()
#     except Exception as e:
#         logger.fatal("EMERGENCY! An exception occurred during worker dispatch: {}".format(str(e)))
#
