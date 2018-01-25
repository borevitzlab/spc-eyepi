#!/usr/bin/python3

import logging.config
import os
import subprocess
import sys
import pyudev
from libs.Camera import *
from libs.Updater import Updater
from libs.Uploader import Uploader, GenericUploader
from libs.Chamber import Chamber
from libs.Sensor import SenseHatMonitor, DHTMonitor
from threading import Lock
import re
from zlib import crc32
import string
import random
import traceback

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
    logger = logging.getLogger("WORKER_DISPATCH")
except Exception as e:
    print("COULDNT SET UP LOGGING WTF")
    pass

def detect_picam_info():
    """
    Detects the existence of a picam

    returns only the info to create a thread.
    """
    logger.info("Detecting picamera")
    if not (os.path.exists("/opt/vc/bin/vcgencmd")):
        logger.error("vcgencmd not found, cannot detect picamera.")
        return dict()


    try:
        cmdret = subprocess.check_output("/opt/vc/bin/vcgencmd get_camera", shell=True).decode()
        if "detected=1" in cmdret:
            return {SysUtil.default_identifier(prefix="picam"): True}
        else:
            return dict()
    except subprocess.CalledProcessError as e:
        logger.error("Couldn't detect picamera. Error calling vcgencmd. {}".format(str(e)))
        pass
    except Exception as e:
        logger.error("General Exception in picamera info detection. {}".format(str(e)))
    return dict()


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
            camera = PiCamera(SysUtil.default_identifier(prefix="picam"), queue=updater.communication_queue)
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


def detect_gphoto_info():
    """
    detects cameras connected via gphoto2 command line.

    this can potentially cause long wait times if a camera is attempting to capture for the split second that it tries
    to gphoto2, however it is the preferred method because it is the most robust and comparmentalised.

    :param type:
    :return: a dict of port:serialnumber values corresponding to the currently connected gphoto2 cameras.
    """
    try:
        cams = {}
        # check output of the --auto-detect gphoto2 command.
        detect_ret = subprocess.check_output(["/usr/bin/gphoto2",
                                              "--auto-detect"],
                                             universal_newlines=True)
        # iterate over the results, should be in the format of [(bus, addr), (bus, addr) ...] for usb connected dslrs
        # this regex matches occurrences of "usb:" followed by 2 comma separated digits.
        for bus, addr in re.findall(r'usb:(\d+),(\d+)', detect_ret):
            try:
                # findall returns strings...
                bus, addr = int(bus), int(addr)
                # this is the format that gphoto2 expects the port to be in.
                port = "usb:{0:03d},{1:03d}".format(bus, addr)

                # gphoto2 command to get the serial number for the DSLR
                # WARNING: when the port here needs to be correct, because otherwise gphoto2 will return values from
                # an arbitrary camera
                sn_detect_ret = subprocess.check_output(['/usr/bin/gphoto2',
                                                         '--port={}'.format(port),
                                                         '--get-config=serialnumber'],
                                                        universal_newlines=True)

                # Match the serial number.
                # this regex can also be used to parse the values from --get-config as all results are returned like this:
                # Label: Serial Number
                # Type: TEXT
                # Current: 4fffa81fed8f40d286a63fce62598ef0
                sn_match = re.search(r'Current: (.*)', sn_detect_ret)

                if not sn_match:
                    # we didnt match any output from the command
                    logger.error("Couldnt match serial number from gphoto2 output. {}".format(port))
                    continue
                sn = sn_match.group(1)

                if sn.lower() == 'none':
                    # there is a bug in a specific version of gphoto2 that causes it to return 'None' for the camera serial
                    # number. If we cant get a unique serial number, we are screwed for multicamera
                    # todo: allow this if there is only one camera.
                    logger.error("serial number matched with value of 'none' {}".format(port))
                    continue

                # pad the serialnumber to 32
                identifier = SysUtil.default_identifier(prefix=sn)
                cams[identifier] = (bus, addr)
            except:
                traceback.print_exc()
                logger.error("Exception detecting gphoto2 camera")
                logger.error(traceback.format_exc())
        return cams
    except subprocess.CalledProcessError as e:
        traceback.print_exc()
        logger.error("Subprocess error detecting gphoto2 cameras")
        logger.error(traceback.format_exc())
    return dict()

def detect_gphoto(updater):
    """
    detects cameras connected via gphoto2 command line.

    this can potentially cause long wait times if a camera is attempting to capture for the split second that it tries
    to gphoto2, however it is the preferred method because it is the most robust and comparmentalised.

    :param type:
    :return: a dict of port:serialnumber values corresponding to the currently connected gphoto2 cameras.
    """
    try:
        workers = []
        # check output of the --auto-detect gphoto2 command.
        cams = detect_gphoto_info()
        for identifier, (bus, addr) in cams.items():
            try:
                port = "usb:{},{}".format(bus, addr)

                # create the camera with the specific bus and addr
                camera = GPCamera(identifier=identifier,
                                  usb_address=(bus, addr),
                                  queue=updater.communication_queue)
                # make sure the updater knows whats going on
                updater.add_to_temp_identifiers(camera.identifier)
                # create an uploader
                uploader = Uploader(camera.identifier, queue=updater.communication_queue)
                workers.extend([camera, uploader])
                logger.debug("Sucessfully detected {} @ {}".format(identifier, port))
                print("Sucessfully detected {} @ {}".format(identifier, port))
            except:
                traceback.print_exc()
                logger.error("Exception creating gphoto2 camera threads")
                logger.error(traceback.format_exc())

        # return start_workers(tuple(workers))
    except subprocess.CalledProcessError as e:
        traceback.print_exc()
        logger.error("Subprocess error detecting gphoto2 cameras")
        logger.error(traceback.format_exc())
    return tuple


def detect_libgphoto(updater: Updater):
    """
    this method has been deprecated until there is a better method of avoiding horrible memory leaks and issue with
    libgphoto2 and gphoto2-cffi.

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
                camera = GPCamera(identifier=identifier,
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
                camera = USBCamera(identifier=identifier,
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
                if i == "SenseHatMonitor":
                    shat = SenseHatMonitor(identifier=SysUtil.get_hostname() + "-SenseHatMonitor",
                                           queue=updater.communication_queue)
                    ul = GenericUploader(shat.identifier, shat.output_dir, "sftp.traitcapture.org")
                    ul.remove_source_files = False
                    workers.append(shat)
                    workers.append(ul)
                else:
                    shat = DHTMonitor(identifier=SysUtil.get_hostname() + "-" + i,
                                      queue=updater.communication_queue)
                    ul = GenericUploader(shat.identifier, shat.output_dir, "sftp.traitcapture.org")
                    ul.remove_source_files = False
                    workers.append(shat)
                    workers.append(ul)
            except Exception as exc:
                logger.error("Couldnt detect a sensor: {}".format(str(exc)))
        return start_workers(tuple(workers))
    except Exception as e:
        logger.error("Couldnt detect sensors for some reason: {}".format(str(e)))
    return tuple()


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
        camera = IVPortCamera(SysUtil.default_identifier(prefix="ivport"), queue=updater.communication_queue)
        updater.add_to_identifiers(camera.identifier)
        workers.append(camera)
        workers.append(Uploader(SysUtil.default_identifier(prefix="ivport"), queue=updater.communication_queue))
    return start_workers(workers)


def get_default_camera_conf(ident):
    return {
        'name': ident,
        'interval': 300,
        'starttime': "05:00",
        'stoptime': "22:00",
        'resize': True,
        'output_dir': "/home/images/{}".format(ident)
    }

def load_config(data: dict = dict()):
    """
    loads a configuration file

    :param data: data to write to the config file before reload/
    :return:
    """
    hostname = SysUtil.get_hostname()
    config_path = "/home/spc-eyepi/{}.yml".format(hostname)
    if not os.path.isfile(config_path):
        SysUtil.write_global_config(data)
    if data:
        SysUtil.write_global_config(data)
    return yaml.load(open(config_path)) or dict()


def run_from_global_config(updater: Updater) -> tuple:
    """
    Runs the startup from a yaml file defining the devices connected to the raspberry pi.

    :param updater:
    :return:
    """
    workers = []
    hostname = SysUtil.get_hostname()
    config_path = "/home/spc-eyepi/{}.yml".format(hostname)

    config_data = load_config()
    camera_confs = config_data.get("cameras", dict())

    """
    PiCamera detect
    """
    logger.info("Detecting picamera")
    picamera_info = detect_picam_info()
    if picamera_info:
        try:
            ident = list(picamera_info.keys())[0]
            section = camera_confs.get(ident, get_default_camera_conf(ident))

            camera = PiCamera(identifier=ident,
                              config=section,
                              queue=updater.communication_queue)

            updater.add_to_identifiers(camera.identifier)
            uploader = Uploader(identifier=camera.identifier,
                                config=section,
                                queue=updater.communication_queue)
            workers.append(camera)
            workers.append(uploader)
            if not config_data.get("cameras"):
                config_data['cameras'] = dict()
            config_data['cameras'][ident] = section
            config_data = load_config(config_data)

        except Exception as e:
            logger.error("General Exception in picamera detection. {}".format(str(e)))
            logger.error(traceback.format_exc())

    """
    DSLR detect
    """
    logger.info("Detecting DSLRs")

    dslr_info = detect_gphoto_info()

    for ident, (bus, addr) in dslr_info.items():
        try:
            section = camera_confs.get(ident, get_default_camera_conf(ident))

            camera = GPCamera(ident,
                              usb_address=(bus, addr),
                              config=section,
                              queue=updater.communication_queue)
            updater.add_to_temp_identifiers(camera.identifier)
            workers.append(camera)
            if section.get("upload", None) is not None:
                uploader = Uploader(camera.identifier,
                                    config=section,
                                    queue=updater.communication_queue)
                workers.append(uploader)
            if not config_data.get("cameras"):
                config_data['cameras'] = dict()
            config_data['cameras'][ident] = section
            config_data = load_config(config_data)
            logger.debug("Sucessfully detected {} @ {}:{}".format(ident, bus, addr))
        except Exception as e:
            logger.error("Couldnt detect DSLR from global yaml {}".format(str(e)))
            logger.error(traceback.format_exc())

    """
    WebCamera detect
    """
    try:
        logger.info("Detecting USB web cameras.")
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
            section = camera_confs.get(identifier, get_default_camera_conf(identifier))
            try:
                # logger.warning("adding {} on {}".format(identifier, sys_number))
                camera = USBCamera(identifier,
                                   config=section,
                                   sys_number=sys_number,
                                   queue=updater.communication_queue)
                updater.add_to_temp_identifiers(camera.identifier)
                workers.append(camera)
                if section.get("upload", None) is not None:
                    workers.append(Uploader(identifier,
                                            config=section,
                                            queue=updater.communication_queue))
                if not config_data.get("cameras"):
                    config_data['cameras'] = dict()
                config_data['cameras'][ident] = section
                config_data = load_config(config_data)
            except Exception as e:
                logger.error("Unable to start usb webcamera {} on {}".format(identifier, sys_number))
                logger.error("{}".format(str(e)))
    except Exception as e:
        logger.error("couldnt detect usb cameras {}".format(str(e)))
        logger.error(traceback.format_exc())

    """
    Sensor detect
    """

    for sensor_type, section in config_data.get("sensors", dict()).items():
        try:
            if sensor_type.lower() == "SenseHatMonitor":
                sensor = SenseHatMonitor("{}-{}".format(SysUtil.get_hostname(), sensor_type),
                                         config=section,
                                         queue=updater.communication_queue)
                workers.append(sensor)
                if section.get("upload", None) is not None:
                    ul = Uploader(sensor.identifier,
                                  config=section,
                                  queue=updater.communication_queue)
                    ul.remove_source_files = False
                    workers.append(ul)
            else:
                sensor = DHTMonitor("{}-{}".format(SysUtil.get_hostname(), sensor_type),
                                    config=section,
                                    queue=updater.communication_queue)
                workers.append(sensor)
                if section.get("upload", None) is not None:
                    ul = Uploader(sensor.identifier,
                                  config=section,
                                  queue=updater.communication_queue)
                    ul.remove_source_files = False
                    workers.append(ul)
        except Exception as e:
            logger.error("Couldnt create sensor from global yaml {}".format(str(e)))
            logger.error(traceback.format_exc())

    """
    Chamber detect
    """
    chamber_conf = config_data.get("chamber", None)
    if chamber_conf:
        if chamber_conf.get("datafile", None):
            chamber = Chamber(identifier=chamber_conf.get("name"),
                              config=chamber_conf)
            workers.append(chamber)
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
        try:
            thread.daemon = True
            thread.start()
        except Exception as e:
            logger.error(traceback.format_exc())
            raise e
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

    logger.info("Program startup...")
    # The main loop for capture

    # these should be all detected at some point.
    updater = None
    workers = tuple()
    try:
        # start the updater. this is the first thing that should happen.
        logger.debug("Starting up the updater")
        updater = Updater()
        start_workers((updater,))
        hostname = SysUtil.get_hostname()
        checksum = SysUtil.get_checksum("{}.yml".format(hostname))
        recent = time.time()
        try:
            workers = run_from_global_config(updater)
        except Exception as e:
            logger.fatal(e)
            traceback.print_exc()
        updater.go()
        # enumerate the usb devices to compare them later on.
        glock = Lock()


        def recreate(action, event):
            # thes all need to be "globalised"
            global glock
            global workers
            global checksum
            global hostname
            global recent
            try:
                # use manual global lock.
                # this callback is from the observer thread, so we need to lock shared resources.
                if time.time() - 10 > recent:
                    with glock:
                        logger.warning("Recreating workers, {}".format(action))
                        kill_workers(workers)
                        workers = run_from_global_config(updater)
                        checksum = SysUtil.get_checksum("{}.yml".format(hostname))
            except Exception as e:
                logger.fatal(e)
                traceback.print_exc()


        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        observer = pyudev.MonitorObserver(monitor, recreate)
        observer.start()

        while True:
            try:
                if checksum != SysUtil.get_checksum("{}.yml".format(hostname)):
                    recreate("config_change", None)
                    checksum = SysUtil.get_checksum("{}.yml".format(hostname))
                time.sleep(60 * 60 * 12)
            except (KeyboardInterrupt, SystemExit) as e:
                kill_workers(workers)
                raise e
            except Exception as e:
                logger.fatal(traceback.format_exc())
                logger.fatal("EMERGENCY! Other exception encountered. {}".format(str(e)))

    except (KeyboardInterrupt, SystemExit):
        print("exiting...")
        kill_workers(workers)
        kill_workers([updater])
        sys.exit()
    except Exception as e:
        traceback.print_exc()
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
