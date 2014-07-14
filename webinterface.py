#!/usr/bin/python
from flask import Flask, redirect, url_for

app = Flask(__name__)
@app.route('/')
def index():
    return '<img src=' + url_for('static',filename='last_image.jpg') + '>' 
      
if __name__ == "__main__":
    app.run(host='0.0.0.0') 
