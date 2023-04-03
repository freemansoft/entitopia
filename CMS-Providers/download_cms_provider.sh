#!/usr/bin/env bash
# assumes in top level

mkdir -p configuration
mkdir -p data
mkdir -p data/doctors-clinicians

curl https://data.cms.gov/provider-data/sites/default/files/resources/69a75aa9d3dc1aed6b881725cf0ddc12_1678493120/DAC_NationalDownloadableFile.csv --output data/doctors-clinicians/DAC_NationalDownloadableFile.csv

