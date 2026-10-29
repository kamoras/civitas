"""The channels a stored post goes to beside Bluesky (app/broadcast.py):
Threads and Instagram, both through Meta's container-then-publish API
(graph.py), one module per platform. Each module answers `configured()`
and `send(post, image_url)`; broadcast decides which posts and when."""
