"""Election-night results: the live count (sync), the seat-flip DEVELOPING
issue (signals) and the election-night Bluesky posts (bluesky).

Deliberately outside app/pipeline/. Nothing here classifies, scores or
learns a label — a count is stored as the state published it and every
sentence is a template around it — and every file under app/pipeline/
(fetch/ aside) feeds the analysis-code fingerprint
(senate_pipeline._compute_analysis_code_hash), whose change clears the
learning store, the analysis cache and the reference corpus. Living here,
an election-night fix never costs the classifiers their self-training.
The per-vendor readers stay beside their primary adapters in
pipeline/fetch/, which the fingerprint already leaves out.
"""
