import resource
import logging
import time
import gphoto2cffi as gp
import pyudev
from libs.SysUtil import SysUtil
from libs.Camera import ThreadedGPCamera


logger = logging.getLogger("tests")
logger.info("Program startup")

def enumerate_usb_devices():
    """
    usb dev list is very important
    :return:
    """
    return set(pyudev.Context().list_devices(subsystem="usb"))

def detect_gphoto():
    """
    creates a list of gphoto2 cameras
    :return:
    """
    try:
        cameras = gp.list_cameras()
        # this is something else...
        workers = []
        for c in cameras:
            try:
                identifier = SysUtil.default_identifier(prefix=c.status.serialnumber)
                camera = ThreadedGPCamera(identifier=identifier,
                                          usb_address=c._usb_address)
                workers.append(camera)
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
    for thread in worker_objects:
        thread.daemon = True
        thread.start()
    return worker_objects


def kill_workers(objects):
    """
    calls the stop method of the workers (they should all implement this)
    :param objects:
    :return:
    """
    for thread in objects:
        thread.stop()


if __name__ == "__main__":

    time_to_test_for = 1200
    gpcameras = detect_gphoto()
    usb_devices = enumerate_usb_devices()
    t = time.time()

    ruinitial = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss+resource.getrusage(resource.RUSAGE_THREAD).ru_maxrss+resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    logger.info("Initial memory usage: {}".format(ruinitial))
    runow = 0
    while t < time.time()+time_to_test_for:
        rudiff = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss+resource.getrusage(resource.RUSAGE_THREAD).ru_maxrss+resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - runow
        runow = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss+resource.getrusage(resource.RUSAGE_THREAD).ru_maxrss+resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        logger.info("Memory usage now {}\t diff: {}".format(runow, rudiff))
        if usb_devices != enumerate_usb_devices():
            logger.error("change in usb dev list")
            kill_workers(gpcameras)
            gphoto_workers = detect_gphoto()
            # should redetect webcams.
            usb_devices = enumerate_usb_devices()
        time.sleep(1)
        t = time.time()
    else:
        kill_workers(gpcameras)
