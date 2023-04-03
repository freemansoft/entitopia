#!/usr/bin/env bash
# assumes in top level

mkdir -p configuration
mkdir -p data
mkdir -p data/carriers
mkdir -p data/inspections
mkdir -p data/crashes

curl https://ai.fmcsa.dot.gov/SMS/files/FMCSA_CENSUS1_2023Feb.zip --output data/carriers/FMCSA_CENSUS1_2023Feb.zip
curl ftp://ftp.senture.com/Inspection_2023Feb.zip --output data/inspections/Inspection_2023Feb.zip
curl ftp://ftp.senture.com/Crash_2023Feb.zip --output data/crashes/Crash_2023Feb.zip

unzip data/carriers/FMCSA_CENSUS1_2023Feb.zip -d data/carriers
unzip data/inspections/Inspection_2023Feb.zip -d data/inspections
unzip data/crashes/Crash_2023Feb.zip -d data/crashes
