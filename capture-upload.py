#!/usr/bin/python3

import logging.config
import os
import re
import subprocess
import sys
import time
import pyudev
import queue
from libs.Camera import *
from libs.Updater import Updater
from libs.Uploader import Uploader
from threading import Lock

__author__ = "Gareth Dunstone"
__copyright__ = "Copyright 2016, Borevitz Lab"
__credits__ = ["Gareth Dunstone", "Tim Brown", "Justin Borevitz", "Kevin Murray", "Jack Adamson"]
__license__ = "GPL"
__version__ = "3.2.8"
__maintainer__ = "Gareth Dunstone"
__email__ = "gareth.dunstone@anu.edu.au"
__status__ = "Feature rollout"


logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)

def detect_picam(updater):
    """
    detects whether the pi has a picam installed and enabled.
    on all SPC-OS devices this will return true if the picam is installed
    on other rpis it may return false if the raspberrypi-firmware-tools is not installed or the boot.cfg flag
    for the camera is not set.

    TODO:
    THIS SHOULD FAIL IF IVPORT IS DETECTED!!!
    :param q: thread safe updater queue
    :return:
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


def detect_ivport(updater):
    """
    meant to detect ivport, uninplemented as of 14/06/2016
    :param updater:
    :return:
    """
    return tuple()


def detect_webcam(updater):
    """
    meant to detect webcams, NOW IMPLEMENTED!!!! YAY!
    :param updater: updater to send information to.
    :return:
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
        return start_workers(workers)
    except Exception as e:
        logger.error("couldnt detect the usb cameras {}".format(str(e)))
    return tuple()


def detect_gphoto(updater):
    """
    detects gphoto cameras and creates thread workers for them.
    :param q: thread safe updater queue object.
    :return:
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
        return start_workers(workers)
    except Exception as e:
        logger.error("Detecting gphoto cameras failed {}".format(str(e)))


def start_workers(worker_objects):
    """
    Starts threaded workers
    :param worker_objects:
    :return:
    """
    logger.debug("Starting {} worker threads".format(str(len(worker_objects))))
    for thread in worker_objects:
        thread.daemon = True
        thread.start()
    return worker_objects


def kill_workers(worker_objects):
    """
    calls the stop method of the workers (they should all implement this)
    :param worker_objects:
    :return:
    """
    logger.debug("Killing {} worker threads".format(str(len(worker_objects))))
    for thread in worker_objects:
        thread.stop()


def enumerate_usb_devices():
    """
    Gets a set of the current usb devices from pyudev
    :return:
    """
    return set(pyudev.Context().list_devices(subsystem="usb"))

if __name__ == "__main__":
    logger = logging.getLogger("Worker_dispatch")
    logger.info("Program startup...")
    # The main loop for capture

    # these should be all detected at some point.
    gphoto_workers = list()
    raspberry = list()
    webcams = list()
    updater = None
    try:
        # start the updater. this is the first thing that should happen.
        logger.debug("Starting up the updater")
        updater = Updater()
        start_workers((updater,))
        raspberry = detect_picam(updater) or detect_ivport(updater)
        webcams = detect_webcam(updater)

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
                kill_workers([updater])
                raise e
            except Exception as e:
                logger.fatal("EMERGENCY! Other exception encountered. {}".format(str(e)))

    except (KeyboardInterrupt, SystemExit):
        print("exiting...")
        kill_workers(gphoto_workers)
        kill_workers(raspberry)
        kill_workers(webcams)
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
