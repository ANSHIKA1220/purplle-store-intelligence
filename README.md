# Purplle Store Intelligence System

## Overview

An AI-powered Store Intelligence System built for the Purplle Tech Challenge 2026.

The system processes CCTV footage from retail stores, generates visitor and movement events, persists them into a database, and exposes analytics through production-style APIs.

The goal is to transform raw store surveillance footage into actionable business intelligence such as:

* Footfall analytics
* Visitor conversion insights
* Zone engagement analysis
* Customer dwell-time analysis
* Revenue correlation
* Store performance metrics

---

## Problem Statement

Retail stores generate large volumes of CCTV footage every day, but most of this data remains unused.

This project converts video streams into structured events and analytics that help answer business questions such as:

* How many customers entered the store?
* Which areas receive the highest engagement?
* How long do customers spend browsing products?
* Which zones contribute most to conversions?
* What is the relationship between customer traffic and revenue?

---

## Features Implemented

### Backend Analytics

* Event Ingestion API
* Session Builder
* Store Metrics API
* Funnel Analytics API
* Heatmap Analytics API
* Anomaly Detection API
* POS Transaction Integration
* SQLite Data Persistence

### Computer Vision Pipeline

* YOLOv8 Person Detection
* ByteTrack Multi-Object Tracking
* Entry Detection
* Exit Detection
* Zone Analytics
* Dwell Time Analytics
* CV Event Persistence

### Event Types Generated

* ENTRY
* EXIT
* ZONE_CHANGE
* DWELL_TIME

---

## Architecture

```text
Store Cameras
      │
      ▼
YOLOv8 Detection
      │
      ▼
ByteTrack Tracking
      │
      ▼
Event Generator
      │
      ▼
SQLite Database
      │
      ▼
FastAPI Analytics Layer
      │
      ▼
Dashboard / Business Insights
```

---

## Technology Stack

### Backend

* FastAPI
* SQLAlchemy
* SQLite

### Computer Vision

* YOLOv8
* OpenCV
* Supervision
* ByteTrack

### Data Processing

* Pandas
* NumPy

### APIs

* REST APIs
* JSON Event Pipeline

---

## Project Structure

```text
store-intelligence/

├── app/
│   ├── api/
│   ├── models/
│   ├── database.py
│   └── main.py
│
├── pipeline/
│   ├── run_entry_event.py
│   ├── run_zone.py
│   ├── run_dwell_time.py
│   ├── event_writer.py
│   ├── tracker.py
│   └── zones.py
│
├── scripts/
│   ├── load_pos.py
│   ├── test_cv_event.py
│   └── check_cv_events.py
│
└── README.md
```

---

## Computer Vision Pipeline

### Entry Detection

The entry camera is processed using:

* YOLOv8 person detection
* ByteTrack tracking
* Virtual line-crossing logic

Generated events:

```json
{
  "event_type": "ENTRY",
  "track_id": 21
}
```

```json
{
  "event_type": "EXIT",
  "track_id": 21
}
```

---

### Zone Analytics

Store zones are defined as:

* LEFT_SHELF
* CENTER_AISLE
* RIGHT_SHELF

Zone transition example:

```json
{
  "event_type": "ZONE_CHANGE",
  "track_id": 9,
  "from": "CENTER_AISLE",
  "to": "RIGHT_SHELF"
}
```

---

### Dwell Time Analytics

Measures customer engagement within store zones.

Example:

```json
{
  "event_type": "DWELL_TIME",
  "track_id": 15,
  "zone": "CENTER_AISLE",
  "seconds": 0.44
}
```

---

## Database Schema

### Event Table

Stores raw event data from the challenge dataset.

### Session Table

Stores visitor sessions and session analytics.

### POS Transactions Table

Stores purchase information and revenue data.

### CV Events Table

Stores generated computer vision events.

Fields:

* event_type
* track_id
* zone
* value
* timestamp

---

## APIs

### Event Ingestion

```http
POST /events/ingest
```

Ingests store events into the system.

---

### Store Metrics

```http
GET /stores/{store_id}/metrics
```

Returns:

* Revenue
* Transactions
* Conversion metrics

---

### Funnel Analytics

```http
GET /stores/{store_id}/funnel
```

Provides visitor funnel statistics.

---

### Heatmap Analytics

```http
GET /stores/{store_id}/heatmap
```

Returns zone activity information.

---

### Anomaly Analytics

```http
GET /stores/{store_id}/anomalies
```

Detects unusual store activity.

---

### CV Summary

```http
GET /cv/summary
```

Returns:

```json
{
  "entries": 11,
  "exits": 3,
  "zone_changes": 3,
  "dwell_events": 3
}
```

---

### Zone Analytics

```http
GET /cv/zones
```

Returns zone transition counts.

---

### Dwell Analytics

```http
GET /cv/dwell
```

Returns cumulative dwell times by zone.

---

## Sample Analytics

### Store Metrics

```json
{
  "store_id": "ST1008",
  "unique_visitors": 0,
  "total_revenue": 34331.71,
  "total_transactions": 101,
  "conversion_rate": 0
}
```

### CV Summary

```json
{
  "entries": 11,
  "exits": 3,
  "zone_changes": 3,
  "dwell_events": 3
}
```

---

## Running the Project

### Create Virtual Environment

```bash
python -m venv venv
```

### Activate

Windows:

```bash
venv\Scripts\activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Run Backend

```bash
uvicorn app.main:app --reload
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

---

## Future Improvements

* Real-time video streaming
* Queue analytics
* Billing area intelligence
* Cross-camera re-identification
* Streamlit dashboard
* Kafka-based event streaming
* Multi-store analytics
* Real-time alerting

---

## Challenge Compliance

The repository intentionally excludes:

* CCTV videos
* Raw datasets
* Large model files

in accordance with the challenge guidelines.

---

## Author

Anshika Shrivastava

Purplle Tech Challenge 2026 Submission
