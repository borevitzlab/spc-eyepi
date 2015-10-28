#!/usr/bin/python3

import os
import subprocess
import sys
import time
import re
import logging
import logging.config

import pyudev

from libs.Camera import GphotoCamera, PiCamera
from libs.Bootstrapper import Bootstrapper
from libs.Uploader import Uploader

logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)

def detect_cameras(type):
    """ 
    detect cameras:
        args= string
            - type to search for in the output of gphoto2, enables the searching of serial cameras and maybe webcams.
    """
    try:
        a = subprocess.check_output("gphoto2 --auto-detect", shell=True).decode()
        a = a.replace(" ","").replace("\n","").replace("-","")
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
        logger.error("Could not detect camera for some reason: %s" % str(e))
    return None


def redetect_cameras(camera_workers):
    """
    this isnt used. but might be, if you want to change stuff of the cameras threads.
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
                if camera_worker.__name__ == serialnumber:
                    camera_worker.camera_port = port
                    logger.info("redetected camera: " + str(serialnumber) + " : " + str(port))
        return True
    except Exception as e:
        print((str(e)))
        logger.error("Could not detect camera for some reason: " + str(e))
        return False



def detect_picam():
    try:
        cmdret = subprocess.check_output("/opt/vc/bin/vcgencmd get_camera", shell=True).decode()
        if cmdret[cmdret.find("detected=") + 9: len(cmdret) - 1] == "1":
            return True
        else:
            return False
    except subprocess.CalledProcessError:
        pass
    return None


def create_workers(cameras):
    camthreads = []
    uploadthreads = []
    for port, serialnumber in list(cameras.items()):
        camthreads.append(
            GphotoCamera(os.path.join("configs_byserial", serialnumber + ".ini"), camera_port=port,
                         serialnumber=serialnumber,
                         name=serialnumber))
        uploadthreads.append(
            Uploader(os.path.join("configs_byserial", serialnumber + ".ini"), name=serialnumber + "-Uploader"))
    return (camthreads, uploadthreads)


def start_workers(objects):
    for thread in objects:
        thread.daemon = True
        thread.start()


def kill_workers(objects):
    for thread in objects:
        thread.stop()
        thread.join()


def get_usb_dev_list():
    context = pyudev.Context()
    ret = ""
    for device in context.list_devices(subsystem='usb'):
        ret += str(device)


if __name__ == "__main__":
    logger = logging.getLogger("Worker_dispatch")
    logger.debug("Program startup")
    # The main loop for capture
    cameras = None
    has_picam = None
    # TODO: Fix storage for multiple cameras
    try:
        tries = 0
        has_picam = detect_picam()

        if has_picam:
            raspberry = [PiCamera("picam.ini", name="PiCam"), Uploader("picam.ini", name="PiCam-Uploader")]
            start_workers(raspberry)

        if os.path.isfile("picam.ini"):
            bootstrapper = Bootstrapper()
            bootstrapper.start()

        while not cameras and tries < 10:
            logger.debug("detecting Cameras")
            cameras = detect_cameras("usb")
            time.sleep(5)
            tries += 1

        if not cameras is None:
            workers = create_workers(cameras)
            start_workers(workers[0])
            start_workers(workers[1])

        usb_dev_list = get_usb_dev_list()
        while True:
            try:
                if usb_dev_list != get_usb_dev_list():
                    cameras = detect_cameras("usb")
                    kill_workers(workers[0])
                    kill_workers(workers[1])
                    # start workers again
                    time.sleep(60)
                    workers = create_workers(cameras)
                    start_workers(workers[0])
                    start_workers(workers[1])
                    usb_dev_list = get_usb_dev_list()
                time.sleep(1)
            except (KeyboardInterrupt, SystemExit):
                if cameras:
                    kill_workers(workers[0])
                    kill_workers(workers[1])
                if has_picam:
                    kill_workers(raspberry)
                bootstrapper.join()
                sys.exit()

    except (KeyboardInterrupt, SystemExit):
        print("exiting...")
        if not cameras == None:
            kill_workers(workers[0])
            kill_workers(workers[1])
        if has_picam:
            kill_workers(raspberry)
        if os.path.isfile("picam.ini"):
            kill_workers([bootstrapper])

        sys.exit()
