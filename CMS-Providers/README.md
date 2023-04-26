
# Purpose
Provide a large data set using medicare related data.  

# Open Items
1. No enrichment exists
1. affiliations extend to more than hospitals

# Data

## doctors-clinicians
Broke out the clinician pipeline and indexing into their own steps so playing with the index would just be a --step with no --phase

Individual doctors or clinicians in `doctor-clinicians` have more than one entry depending on how many registrations are captured.

1. Auto-generated clinician index `_id` because the same ids (NPI, Ind_PAC_ID, Ind_enrl_ID) appear more than one row due to multiple hospitals or other registrations

## facillity-affiliations
This is more than just `Hospital` affiliations.
1. Auto-generated `_id` because no unique combination in data set

## hospitals
1. Hospitals `_ID` is populated by `Facility ID`

# References
* Medicare Providers https://data.cms.gov/provider-data/
* Data Dictionary https://data.cms.gov/provider-data/sites/default/files/data_dictionaries/physician/DOC_Data_Dictionary.pdf
