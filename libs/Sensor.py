import datetime
import logging.config
import os
import time
from collections import deque

from threading import Thread, Event
from libs.SysUtil import SysUtil
import csv, json
from sense_hat import SenseHat
logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)


class Sensor(object):
    accuracy = 1
    output_types = ["json", 'csv', 'tsv']
    def __init__(self, identifier: str=None, queue: deque=None, write_out=True, **kwargs):
        # identifier is NOT OPTIONAL!

        self.communication_queue = queue or deque(tuple(), 256)
        self.logger = logging.getLogger(identifier)
        self.stopper = Event()
        self.identifier = identifier
        # interval in seconds
        self.interval = 30
        # chunking interval in number of datapoints
        self.chunking_interval = 2**16
        # setup a deque of measurements
        self.measurements = deque(maxlen=self.chunking_interval)
        self.write_out = write_out


        self.data_directory = os.path.join(os.getcwd(), "sensors", self.identifier)
        if write_out:
            if not os.path.exists(self.data_directory):
                os.makedirs(self.data_directory)
        self.current_capture_time = datetime.datetime.now()
        self.failed = list()

    @staticmethod
    def timestamp(tn: datetime.datetime):
        """
        creates a properly formatted timestamp.
        :param tn: datetime to format to timestream timestamp string
        :return:
        """
        st = tn.strftime('%Y_%m_%d_%H_%M_%S')
        return st

    @staticmethod
    def time2seconds(t: datetime):
        """
        converts a datetime to an integer of seconds since epoch
        """
        try:
            return int(t.timestamp())
        except:
            # only implemented in python3.3
            # this is an old compatibility thing
            return t.hour * 60 * 60 + t.minute * 60 + t.second

    @property
    def timestamped_filename(self):
        """
        builds a timestamped image basename without extension from a datetime.
        :param time_now:
        :return: string image basename
        """
        return '{sensor_name}_{timestamp}'.format(sensor_name=self.identifier,
                                                  timestamp=Sensor.timestamp(self.current_capture_time))

    @property
    def time_to_measure(self):
        """
        filters out times for mesauring, returns True by default
        returns False if the conditions where the sensor should NOT capture are met.
        :return:
        """
        # data capture interval
        if not (self.time2seconds(self.current_capture_time) % self.interval < Sensor.accuracy):
            return False
        return True


    @property
    def time_to_chunk(self):
        """
        filters out times for chunking measurements, returns True by default
        :return:
        """
        # data capture interval
        if not (self.time2seconds(self.current_capture_time) % self.chunking_interval < Sensor.accuracy):
            return False
        return True

    def stop(self):
        """
        stops the thread.
        :return:
        """
        self.stopper.set()

    def communicate_with_updater(self):
        """
        communication member. This is meant to send some metadata to the updater thread.
        :return:
        """
        try:
            data = dict(
                name=self.identifier,
                identifier=self.identifier,
                failed=self.failed
            )
            self.communication_queue.append(data)
            self.failed = list()
        except Exception as e:
            self.logger.error("thread communication error: {}".format(str(e)))

    def run(self):
        while True and not self.stopper.is_set():
            self.current_capture_time = datetime.datetime.now()
            # checking if enabled and other stuff
            if self.time_to_measure:
                try:
                    self.logger.info("Capturing data for {}".format(self.identifier))
                    measurement = self.get_measurement()
                    self.logger.info("Got Measurement {}".format(str(measurement)))
                    self.measurements.append([self.current_capture_time.isoformat(), measurement])
                    self.communicate_with_updater()
                except Exception as e:
                    self.logger.critical("Sensor data error - {}".format(str(e)))
            if self.write_out and self.time_to_chunk:
                with open(os.path.join(self.data_directory, self.timestamped_filename) + ".csv", 'w',
                          newline='') as csvfile:
                    writer = csv.writer(csvfile, dialect=csv.excel)
                    writer.writerow(("datetime", self.identifier))
                    writer.writerows(self.measurements)
                with open(os.path.join(self.data_directory, self.timestamped_filename) + ".tsv", 'w',
                          newline='') as tsvfile:
                    writer = csv.writer(tsvfile, dialect=csv.excel_tab)
                    writer.writerow(("datetime", self.identifier))
                    writer.writerows(self.measurements)
                with open(os.path.join(self.data_directory, self.timestamped_filename) + ".json", 'w',
                          newline='') as jsonfile:
                    jsonfile.write(json.dumps({str(self.identifier): list(self.measurements)}))
            time.sleep(0.1)

    def get_measurement(self):
        """
        override this method with the method of collecting a single measurement
        :return:
        """
        return None


class SenseHatMonitor(Sensor):
    """
    Sensor Group object for the SenseHat
    """
    def __init__(self, identifier: str=None, **kwargs):
        self.sensehat = SenseHat()
        self.display_str = "Init Sensors..."
        self.sensehat.show_message(self.display_str)
        super(SenseHatMonitor, self).__init__(identifier, **kwargs)

    def run(self):
        while True and not self.stopper.is_set():
            self.current_capture_time = datetime.datetime.now()
            # checking if enabled and other stuff
            if self.time_to_measure:
                try:
                    self.logger.info("Capturing data for {}".format(self.identifier))
                    measurement = self.get_measurement()
                    self.logger.info("Got Measurement {}".format(str(measurement)))
                    self.measurements.append([self.current_capture_time.isoformat(), *measurement])
                    self.show_data(measurement)
                    self.communicate_with_updater()
                except Exception as e:
                    self.logger.critical("Sensor data error - {}".format(str(e)))
            if self.write_out and self.time_to_chunk:
                with open(os.path.join(self.data_directory, self.timestamped_filename) + ".csv", 'w',
                          newline='') as csvfile:
                    writer = csv.writer(csvfile, dialect=csv.excel)
                    writer.writerow(("datetime", "temperature", "humidity", "pressure"))
                    writer.writerows(self.measurements)
                with open(os.path.join(self.data_directory, self.timestamped_filename) + ".tsv", 'w',
                          newline='') as tsvfile:
                    writer = csv.writer(tsvfile, dialect=csv.excel_tab)
                    writer.writerow(("datetime", "temperature", "humidity", "pressure"))
                    writer.writerows(self.measurements)
                with open(os.path.join(self.data_directory, self.timestamped_filename) + ".json", 'w',
                          newline='') as jsonfile:
                    data_dict = {
                        "datetime":    [x[0] for x in self.measurements],
                        "temperature": [x[1] for x in self.measurements],
                        "humidity":    [x[2] for x in self.measurements],
                        "pressure":    [x[3] for x in self.measurements]
                    }
                    jsonfile.write(json.dumps(data_dict))
            time.sleep(0.1)

    def show_data(self, measurement):
        try:
            message_str = "T:{0:.2f} H:{1:.2f} P:{2:.2f}"
            self.sensehat.show_message(message_str.format(*measurement))
        except Exception as e:
            self.logger.error(str(e))

    def get_measurement(self):
        """
        get measurements for sensehat
        :return:
        """
        return self.sensehat.temperature, self.sensehat.humidity, self.sensehat.pressure


class ThreadedSensor(Thread):
    """
    threaded implementation of the sensor cclass.
    """
    def __init__(self, *args, **kwargs):
        if hasattr(self, "identifier"):
            Thread.__init__(self, name=self.identifier)
        else:
            Thread.__init__(self)

        print("Threaded startup")
        super(ThreadedSensor, self).__init__(*args, **kwargs)
        self.daemon = True

    def run(self):
        super(Sensor, self).run()


class ThreadedSenseHat(ThreadedSensor, SenseHat):
    """
    threaded implementation of the RPi SenseHat
    """
    def __init__(self, *args, **kwargs):
        SenseHat.__init__(self, *args, **kwargs)
        super(ThreadedSenseHat, self).__init__(*args, **kwargs)

    def run(self):
        super(SenseHat, self).run()