Welcome to SPC-EyePi

The goal of this project is to create a robust open source code base for controlling DSLR and the on-board raspberry pi camera from a Raspberry-PI or similar Linux boards.

**FEATURES**
 * Control of multiple DSLR cameras (works great for two, not tested for more)
 * Upload of JPG, RAW or JPG+RAW images
 * Captured photos are cached locally and  uploaded to a central server of your choice via sftp or ftp. 
 * Cameras settings can be controlled from a web page. 
 * Camera uploads it's IP address along with image for easy finding of the camera on your network
 * Camera uploads a JSON xml file with current capture information for easy camera management. See page below for an example of using this to monitor incoming data from 18 cameras in one web page. http://phenocam.anu.edu.au/cloud/a_data/camupload/eyepi-status.min.php
 * Camera can auto-upload both timestamped and fixed filenames (for useas a webcam or with auto archiving systems requiring a fixed image name).
 * Extensively tested with Canons (600D, 700D, 70D). Works with Nikon D7100 but not extensively tested/

Features in active  development: 
 * Time-series scheduler  to auto-set camera exposure and shutter speed settings (useful for shooting 24-hrs a day in fixed but variable lighting situations like plant growth chambers)
 * Auto Bulb-ramping... we are aspiring to be able to shoot 24-7 DSLR timelapses through sunrise and sunset.
 * DB-based camera management system.
 * Weatherproof and solar powered camera housing  for full remote timelapse solution 


Code automatically detects connected usb DSLRs and the raspberry pi camera (just duplicate "example.ini" to "picam.ini" and "eyepi.ini"). DSLR's are given a unique ID based on hardware serial. Camera name can be easily changed to a user friendly value.

This is also the pulling repo for the spc-eyepi component of SPC-OS.
