import datetime
import logging.config
import os
import time
from collections import deque

from threading import Thread, Event
from libs.SysUtil import SysUtil
import csv, json

logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)

try:
    from sense_hat import SenseHat
except Exception as e:
    logging.error("Couldnt import sensehat: {}".format(str(e)))

try:
    import Adafruit_DHT
except Exception as e:
    logging.error("Couldnt import Adafruit_DHT: {}".format(str(e)))


class Sensor(object):
    """
    Sensor base.
    To use this class you need to override 'get_measurement()' so that it returns a tuple of the measurements that match
    the headers defined in the data_headers classvar.
    by default it will write 5 files, rolling 24 hour files (csv, tsv & json) and all time files that are appended to
    (csv & tsv only)
    """
    accuracy = 1
    data_headers = tuple()

    def __init__(self, identifier: str = None, queue: deque = None, write_out: bool = True, interval: int = 60,
                 **kwargs):
        # identifier is NOT OPTIONAL!
        # data headers need to be set
        self.communication_queue = queue or deque(tuple(), 256)
        self.logger = logging.getLogger(identifier)
        self.stopper = Event()
        self.identifier = identifier
        # interval in seconds
        self.interval = interval
        # chunking interval in number of datapoints
        dlen = 86400 / interval

        # setup a deque of measurements
        self.measurements = deque(maxlen=dlen)
        self.write_out = write_out

        self.data_directory = os.path.join(os.getcwd(), "sensors", self.identifier)
        if write_out:
            if not os.path.exists(self.data_directory):
                os.makedirs(self.data_directory)
        self.current_capture_time = datetime.datetime.now()
        self.failed = list()

    @staticmethod
    def timestamp(tn: datetime.datetime) -> str:
        """
        creates a properly formatted timestamp.
        :param tn: datetime to format to timestream timestamp string
        :return:
        """
        return tn.strftime('%Y_%m_%d_%H_%M_%S')

    @staticmethod
    def time2seconds(t: datetime) -> int:
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
    def timestamped_filename(self) -> str:
        """
        builds a timestamped image basename without extension from a datetime.
        :param time_now:
        :return: string image basename
        """
        return '{sensor_name}_{timestamp}'.format(sensor_name=self.identifier,
                                                  timestamp=Sensor.timestamp(self.current_capture_time))

    @property
    def time_to_measure(self) -> bool:
        """
        filters out times for mesauring, returns True by default
        returns False if the conditions where the sensor should NOT capture are met.
        :return:
        """
        # data capture interval
        if not (self.time2seconds(self.current_capture_time) % self.interval < Sensor.accuracy):
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
                last_measure=self.current_capture_time,
                identifier=self.identifier,
                failed=self.failed
            )
            self.communication_queue.append(data)
            self.failed = list()
        except Exception as e:
            self.logger.error("thread communication error: {}".format(str(e)))

    def write_daily_rolling(self):
        """
        writes full rolling daily daita files.
        :param rows:
        :return:
        """
        try:
            fn = os.path.join(self.data_directory, "{}-daily".format(self.identifier))
            csvf, tsvf, jsonf = fn + ".csv", fn + ".tsv", fn + ".json"

            with open(csvf, 'w', newline='') as csvfile, open(tsvf, 'w', newline='') as tsvfile, open(jsonf, 'w',
                                                                                                      newline='') as jsonfile:
                writer = csv.writer(csvfile, dialect=csv.excel)
                writer.writerow(self.data_headers)
                writer.writerows(self.measurements)
                writer = csv.writer(tsvfile, dialect=csv.excel_tab)
                writer.writerow(self.data_headers)
                writer.writerows(self.measurements)
                d = dict()
                for k in self.data_headers:
                    d[k] = list()
                for measurement in self.measurements:
                    for idx, m in enumerate(measurement[:len(self.data_headers)]):
                        d[self.data_headers[idx]].append(m)
                jsonfile.write(d)
        except Exception as e:
            self.logger.error("Error writing daily rolling data {}".format(str(e)))

    def append_to_alltime(self, measurement: tuple):
        """
        appends the measurement to the csv and tsv files.
        :param measurement:
        :return:
        """
        try:
            fn = os.path.join(self.data_directory, "{}-alltime".format(self.identifier))
            csvf, tsvf, jsonf = fn + ".csv", fn + ".tsv", fn + ".json"
            # write the headers if the files are new.
            if not os.path.exists(csvf):
                with open(csvf, 'w') as csvfile:
                    csvfile.write(",".join(("datetime", *self.data_headers))+"\n")
            if not os.path.exists(tsvf):
                with open(tsvf, 'w') as tsvfile:
                    tsvfile.write("\t".join(("datetime", *self.data_headers))+"\n")
            # append the measurements to the files.
            with open(csvf, 'a') as csvfile, open(tsvf, 'a') as tsvfile:
                csvfile.write(",".join(*measurement)+"\n")
                tsvfile.write("\t".join(*measurement)+"\n")
        except Exception as e:
            self.logger.error("Error appending measurement to the all time data: {}".format(str(e)))

    def run(self):
        """
        run method.
        used for threaded sensors
        :return:
        """
        while True and not self.stopper.is_set():
            self.current_capture_time = datetime.datetime.now()
            # checking if enabled and other stuff
            if self.time_to_measure:
                try:
                    self.logger.info("Capturing data for {}".format(self.identifier))
                    measurement = self.get_measurement()
                    self.logger.info("Got Measurement {}".format(str(measurement)))
                    self.measurements.append([self.timestamp(self.current_capture_time), *measurement])
                    self.append_to_alltime(measurement)
                    self.write_daily_rolling()
                    self.communicate_with_updater()
                except Exception as e:
                    self.logger.critical("Sensor data error - {}".format(str(e)))

            time.sleep(0.1)

    def get_measurement(self):
        """
        override this method with the method of collecting measurements from the sensor
        should return a list or tuple
        :return:
        """
        return tuple()


class DHTMonitor(Sensor):
    """
    Data logger class for DHT11, DHT22 & AM2302 GPIO temperature & humidity sensors from Adafruit.

    supply the identifier and the gpio pi that the sensor is connected to, along with the type of sensor.
    defaults to pin 14, DHT22
    """

    data_headers = ('humidity', "temperature")

    def __init__(self, identifier: str = None, pin: int = 14, sensor_type="AM2302", **kwargs):
        self.pin = pin
        sensor_args = {
            11: Adafruit_DHT.DHT11,
            22: Adafruit_DHT.DHT22,
            2302: Adafruit_DHT.AM2302,
            "11": Adafruit_DHT.DHT11,
            "22": Adafruit_DHT.DHT22,
            "2302": Adafruit_DHT.AM2302,
            "DHT11": Adafruit_DHT.DHT11,
            "DHT22": Adafruit_DHT.DHT22,
            "AM2302": Adafruit_DHT.AM2302,
        }
        self.sensor_type = sensor_args.get(sensor_type, Adafruit_DHT.AM2302)
        super(DHTMonitor, self).__init__(identifier, **kwargs)

    def get_measurement(self) -> tuple:
        """
        gets data from the DHT22
        :return:
        """
        try:
            return Adafruit_DHT.read_retry(self.sensor_type, self.pin)
        except Exception as e:
            self.logger.error("Couldnt get data, {}".format(str(e)))
            return tuple(None for _ in range(len(self.data_headers)))


class SenseHatMonitor(Sensor):
    """
    Data logger class for Astro Pi Sensehat
    No need to supply anything except the identifier as the SenseHad uses some kind of black sorcery to work it out.
    """

    data_headers = ("temperature", "humidity", "pressure")

    def __init__(self, identifier: str = None, **kwargs):
        self.sensehat = SenseHat()
        self.display_str = "Init Sensors..."
        self.sensehat.show_message(self.display_str)
        super(SenseHatMonitor, self).__init__(identifier, **kwargs)

    def show_data(self, measurement):
        try:
            message_str = "T:{0:.2f} H:{1:.2f} P:{2:.2f}"
            self.sensehat.show_message(message_str.format(*measurement))
        except Exception as e:
            self.logger.error(str(e))

    def get_measurement(self) -> tuple:
        """
        get measurements for sensehat
        :return:
        """
        try:
            return self.sensehat.temperature, self.sensehat.humidity, self.sensehat.pressure
        except Exception as e:
            self.logger.error("Couldnt get data, {}".format(str(e)))
            return tuple(None for _ in range(len(self.data_headers)))


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


class ThreadedSenseHat(ThreadedSensor, SenseHatMonitor):
    """
    threaded implementation for the AstroPI SenseHat
    """

    def __init__(self, *args, **kwargs):
        SenseHatMonitor.__init__(self, *args, **kwargs)
        super(ThreadedSenseHat, self).__init__(*args, **kwargs)

    def run(self):
        super(SenseHatMonitor, self).run()


class ThreadedDHT(ThreadedSensor, DHTMonitor):
    """
    threaded implementation for the Adafruit DHT/AM GPIO sensor module
    """

    def __init__(self, *args, **kwargs):
        DHTMonitor.__init__(self, *args, **kwargs)
        super(ThreadedDHT, self).__init__(*args, **kwargs)

    def run(self):
        super(DHTMonitor, self).run()
