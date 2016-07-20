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

__author__ = "Gareth Dunstone"
__copyright__ = "Copyright 2016, Borevitz Lab"
__credits__ = ["Gareth Dunstone", "Tim Brown", "Justin Borevitz", "Kevin Murray", "Jack Adamson"]
__license__ = "GPL"
__version__ = "3.2.6"
__maintainer__ = "Gareth Dunstone"
__email__ = "gareth.dunstone@anu.edu.au"
__status__ = "Testing"


#Not sure of best scope to put this in
CommunicationQueue = queue.Queue

logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)


def detect_gphoto_cameras():
    """
    detects cameras connected via usb/gphoto2.
    locks gphoto2, so this will cause errors if a camera is attempting to capture for the split second that it tries
    to detect

    :param type:
    :return: a dict of port:serialnumber values corresponding to the currently connected gphoto2 cameras.
    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True).decode()
        a = a.replace(" ", "").replace("\n", "").replace("-", "")
        cams = {}
        for pstring in re.finditer("usb:", a):
            port = a[pstring.start():pstring.end() + 7]
            cmdret = subprocess.check_output(
                'gphoto2 --port "' + port + '" --get-config serialnumber',
                shell=True).decode()
            cur = cmdret.split("\n")[-2]
            cams[port] = cur.split(" ")[-1]
        return cams
    except Exception as e:
        logger.error("Could not detect camera for some reason: {}".format(str(e)))
    return None


def redetect_cameras(camera_workers):
    """
    this isnt used yet, but it may be in the future to reassign port numbers to cameras when they are unplugged.
    :param camera_workers:
    :return:
    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True).decode()
        for port in re.finditer("usb:", a):
            cmdret = subprocess.check_output(
                'gphoto2 --port "' + a[port.start():port.end() + 7] + '" --get-config serialnumber',
                shell=True).decode()
            serialnumber = cmdret[cmdret.find("Current: ") + 9: len(cmdret) - 1]
            port = a[port.start():port.end() + 7]
            for camera_worker in camera_workers:
                if camera_worker.identifier == serialnumber:
                    camera_worker.camera_port = port
                    logger.info("redetected camera: " + str(serialnumber) + " : " + str(port))
        return True
    except Exception as e:
        print((str(e)))
        logger.error("Could not detect camera for some reason: " + str(e))
        return False


def detect_picam(q=None):
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
    try:
        cmdret = subprocess.check_output("/opt/vc/bin/vcgencmd get_camera", shell=True).decode()
        if cmdret[cmdret.find("detected=") + len("detected="): len(cmdret) - 1] == "1":
            workers = (PiCamera(default_identifier(prefix="PiCam"), queue=q), Uploader(
                default_identifier(prefix="PiCam"), queue=q))
            return start_workers(workers)
        else:
            return tuple()
    except subprocess.CalledProcessError:
        logger.error("couldnt call picamera command to check. No picam.")
        pass
    except Exception as e:
        logger.error("picam detection: {}".format(str(e)))
    return tuple()

def detect_ivport(q=None):
    """
    meant to detect ivport, uninplemented as of 14/06/2016
    :param q:
    :return:
    """
    return tuple()

def detect_webcam(q=None):
    """
    meant to detect webcams, uninplemented as of 14/06/2016
    :param q:
    :return:
    """
    return tuple()

def detect_gphoto(q=None):
    """
    detects gphoto cameras and creates thread workers for them.
    :param q: thread safe updater queue object.
    :return:
    """
    try:
        cameras = detect_gphoto_cameras()
        # this is something else...
        workers = list(sum(((GphotoCamera(default_identifier(prefix=sn), port, queue=q), Uploader(
            default_identifier(prefix=sn), queue=q)) for port, sn in cameras.items()), ()))
        return start_workers(workers)
    except Exception as e:
        logger.error("Detecting gphoto cameras failed {}".format(str(e)))


def default_identifier(prefix=None):
    """
    returns an identifier, If no prefix available, generates something.
    :param prefix:
    :return:
    """
    if prefix:
        return SysUtil.get_identifier_from_name(prefix)
    else:
        from hashlib import md5
        logger.warning("using autogenerated serialnumber")
        serialnumber = ("AUTO_" + md5(bytes(prefix, 'utf-8')).hexdigest()[len("AUTO_"):])[:32]
        return serialnumber


def start_workers(worker_objects):
    for thread in worker_objects:
        thread.daemon = True
        thread.start()
    return worker_objects


def kill_workers(objects):
    for thread in objects:
        thread.stop()


def enumerate_usb_devices():
    """
    usb dev list is very important
    :return:
    """
    return set(pyudev.Context().list_devices(subsystem="usb"))


if __name__ == "__main__":
    logger = logging.getLogger("Worker_dispatch")
    logger.info("Program startup")
    updater = list()
    # The main loop for capture
    gphoto_workers = list()
    raspberry = list()
    # these should be detected at some point.
    webcams = list()
    # TODO: Fix storage for multiple cameras
    try:
        # start the updater. this is the first thing that should happen.
        updater = Updater()
        updater.start()

        raspberry = detect_picam(q=updater.communication_queue) or detect_ivport(q=updater.communication_queue)
        webcams = detect_webcam(q=updater.communication_queue)

        # try 10 times to detect gphoto cameras. Maybe they arent awake yet.
        for x in range(10):
            logger.debug("detecting Cameras")
            gphoto_workers = detect_gphoto(q=updater.communication_queue)
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
                    logger.error("change in usb dev list")
                    kill_workers(gphoto_workers)

                    gphoto_workers = detect_gphoto(q=updater.communication_queue)

                    usb_devices = enumerate_usb_devices()

                time.sleep(1)
            except (KeyboardInterrupt, SystemExit) as e:
                kill_workers(gphoto_workers)
                kill_workers(raspberry)
                kill_workers(webcams)
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
