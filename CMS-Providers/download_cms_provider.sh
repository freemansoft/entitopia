#!/usr/bin/env bash
# assumes running in CMS-Providers dir

mkdir -p configuration
mkdir -p data

FILE=data/doctors-clinicians/DAC_NationalDownloadableFile.csv
if [ ! -f "$FILE" ]; then
    mkdir -p data/doctors-clinicians
    echo "Downloading $FILE"
    curl https://data.cms.gov/provider-data/sites/default/files/resources/69a75aa9d3dc1aed6b881725cf0ddc12_1678493120/DAC_NationalDownloadableFile.csv --output "$FILE"
fi

FILE=data/hospitals/Hospital_General_Information.csv
if [ ! -f "$FILE" ]; then
    mkdir -p data/hospitals
    echo "Downloading $FILE"
    curl https://data.cms.gov/provider-data/sites/default/files/resources/092256becd267d9eeccf73bf7d16c46b_1681243512/Hospital_General_Information.csv --output "$FILE"
fi

FILE=data/facillity-affiliations/Facility_Affiliation.csv
if [ ! -f "$FILE" ];then
    mkdir -p data/facillity-affiliations
    echo "Downloading $File"
    curl https://data.cms.gov/provider-data/sites/default/files/resources/6dcb0da45dc1f3d4977c195c1df3f397_1680898558/Facility_Affiliation.csv --output "$FILE"
fi