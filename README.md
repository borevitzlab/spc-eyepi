Welcome to SPC-EyePi

This project allows you to do timeseries photography from your
DSLR camera on the Raspberry-PI or other Linux boards with
probably not a lot of changes.

Once photo's are taken they are then uploaded to a central
server of your choice via sftp.

There's a configuration file where you can edit all the settings
such as timelapse intervals and server settings.

Installation

 - Copy install_files/45-libgphoto2.rules to /etc/udev/rules.d

 - Copy install_files/sample.ini to eyepi.ini

 - set the system date and time using dpkg-reconfigure tzdata

Runninng the program

 - Run the command "sudo python timedcapture.py"


 