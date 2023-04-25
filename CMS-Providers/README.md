
# Purpose
Provide a large data set using medicare providers.  `doctor-clinicians`  have more than one entry depending on how many registrations are captured.

# Notes
Broke out the clinician pipeline and indexing into their own steps so playing with the index would just be a --step with no --phase

# Data

## doctors-clinicians
1. Auto-generated clinician index `_id` because the same ids (NPI, Ind_PAC_ID, Ind_enrl_ID) appear more than one row due to multiple hospitals or other registrations


# References
* Medicare Providers https://data.cms.gov/provider-data/
* Data Dictionary https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/physician/DOC_Data_Dictionary.pdf
