apt-get install libusb-dev libltdl-dev libghoto2-2 gvfs-bin python-pip
pip install pysftp
gcc -o dcraw -O4 dcraw.c -lm -ljpeg -llcms2 -DNO_JASPER
