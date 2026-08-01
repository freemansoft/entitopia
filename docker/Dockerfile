# Elasticsearch pinned to the same version requirements.txt pins for the
# Python client (elasticsearch==9.4.1), carrying the two analysis plugins
# entitopia's analyzers depend on.
#
# analysis-phonetic supplies the double_metaphone and beider_morse encoders
# used by carriers' name_phonetic / name_phonetic_bm analyzers.
# analysis-icu supplies icu_normalizer and icu_folding, used by every
# name_clean / street_clean analyzer in both projects.
#
# Without these plugins index creation fails outright: Elasticsearch rejects
# an analyzer referencing an unknown filter type.
FROM docker.elastic.co/elasticsearch/elasticsearch:9.4.1

RUN elasticsearch-plugin install --batch analysis-icu \
 && elasticsearch-plugin install --batch analysis-phonetic
