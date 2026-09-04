# NETRA — Technology Stack (registration form)

| Field | Selection |
|---|---|
| **Frontend** | React |
| **Backend** | FastAPI |
| **Database** | PostgreSQL |
| **Cloud Platform** | On-Premise |

**AI Models**
```
YOLOv8 (vehicle detection), YOLOv8 custom-trained license plate detector, PaddleOCR (plate text recognition), OSNet re-identification embeddings (cross-camera vehicle matching)
```

**APIs Used**
```
ONVIF Profile S device/media API, RTSP streaming, WebRTC and HLS delivery, OpenStreetMap tile API, Leaflet mapping API, internal REST APIs (registry, events, watchlist, alerts), WebSocket alert push
```

**Programming Languages**
```
Python, JavaScript, SQL
```

**Frameworks**
```
PyTorch, OpenCV, Ultralytics, FFmpeg, PaddleOCR, SQLAlchemy, PostGIS, Redis, Leaflet
```

**Other Tools**
```
Docker, Docker Compose, GitHub, MediaMTX, NGINX, Postman, Figma
```

---

## Why this stack (for the HLD, not the form)

- **One language for backend + AI.** FastAPI is Python, the inference pipeline is Python. No IPC bridge, no serialization layer between the API and the model. Solo dev, six days — this is the single biggest time saver.
- **PostgreSQL + PostGIS + Redis.** PostGIS gives Model 1's GIS foundation for free (spatial queries, coverage gap analysis, route geometry). Postgres full-text handles detection search — no Elasticsearch to operate. Redis is the event bus and live alert fanout — no Kafka to operate.
- **On-Premise.** Honest and correct: police video is not leaving the state network. Local RTX GPU runs inference; the same design scales as regional edge nodes shipping metadata, not video.
- **MediaMTX.** Open-source RTSP/WebRTC/HLS relay. Ingests heterogeneous camera protocols and republishes browser-playable WebRTC. This is the piece that makes "unified viewing" real instead of an iframe of a video file.
