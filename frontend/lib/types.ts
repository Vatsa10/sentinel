// Types mirrored from netra/api/app.py response shapes. Curled against a
// running backend on 2026-09-07 rather than guessed; line numbers below
// refer to netra/api/app.py unless noted.

/** GET /api/auth/whoami (Task A1, may 404 on an older backend). */
export interface Who {
  role: "viewer" | "operator" | "admin";
  name: string;
  enabled: boolean;
}

/** Live health of one camera worker (netra/ingest/stream.py:246). */
export interface CameraHealth {
  camera_id: string;
  connected: boolean;
  frames_seen: number;
  frames_emitted: number;
  measured_fps: number;
  reconnects: number;
  loop_cuts: number;
  escalated: boolean;
  last_error: string | null;
  stale_s: number | null;
}

/** GET /api/cameras (app.py:79). */
export interface Camera {
  id: string;
  name: string;
  lat: number;
  lon: number;
  city: string | null;
  district: string | null;
  department: string | null;
  codec: string | null;
  width: number | null;
  height: number | null;
  declared_fps: string | null;
  capability: string;
  health: string;
  capability_note: string | null;
  mean_luma: number | null;
  time_group: string | null;
  whep_url: string | null;
  hls_url: string | null;
  rtsp_url: string | null;
  /** Populated from the live supervisor; empty object when the camera is not running. */
  live: Partial<CameraHealth>;
}

/** One item of GET /api/detections (app.py:269). */
export interface Detection {
  id: number;
  camera_id: string;
  camera_name: string | null;
  lat: number | null;
  lon: number | null;
  at: string;
  pts_ms: number;
  vehicle_class: string | null;
  confidence: number;
  colour: string | null;
  plate_text: string | null;
  plate_conf: number | null;
  plate_chars: string | null;
  plate_votes: number | null;
  evidence: string | null;
  bbox: [number, number, number, number] | null;
  track_id: number | null;
  scene_time: string | null;
  scene_time_corroborated: boolean | null;
  attributes: Record<string, unknown> | null;
}

/** GET /api/detections envelope. */
export interface DetectionPage {
  total: number;
  count: number;
  items: Detection[];
}

/** GET /api/watchlist (app.py:428). */
export interface WatchlistEntry {
  id: number;
  plate: string;
  category: string;
  severity: "low" | "medium" | "high" | "critical" | string;
  owner_name: string | null;
  vehicle_make: string | null;
  vehicle_colour: string | null;
  vehicle_class: string | null;
  case_ref: string | null;
  source_db: string | null;
  notes: string | null;
  active: boolean;
}

/** One reason component inside an Alert's `reasons` object. */
export interface AlertReason {
  score: number;
  detail: string;
}

/** GET /api/alerts (app.py:478). */
export interface Alert {
  id: number;
  at: string;
  camera_id: string;
  camera_name: string | null;
  lat: number | null;
  lon: number | null;
  score: number;
  match_type: string;
  reasons: {
    plate?: AlertReason;
    appearance?: AlertReason;
    policy?: AlertReason;
    [k: string]: AlertReason | undefined;
  };
  severity: "low" | "medium" | "high" | "critical" | string;
  acknowledged: boolean;
  plate_observed: string | null;
  plate_watchlist: string | null;
  category: string | null;
  case_ref: string | null;
  evidence: string | null;
  detection_id: number | null;
  attributes: Record<string, unknown> | null;
}

/** GET /api/zones (app.py:887). */
export interface Zone {
  id: number;
  camera_id: string;
  name: string;
  rule: "intrusion" | "crossing" | "loitering" | string;
  points: [number, number][];
  classes: string[];
  severity: "low" | "medium" | "high" | "critical" | string;
  dwell_s: number;
  active: boolean;
}

/** GET /api/zones/events (app.py:955). */
export interface ZoneEvent {
  id: number;
  at: string;
  camera_id: string;
  camera_name: string | null;
  lat: number | null;
  lon: number | null;
  zone: string | null;
  rule: string;
  object_class: string | null;
  direction: string | null;
  detail: string | null;
  severity: "low" | "medium" | "high" | "critical" | string;
  evidence: string | null;
  acknowledged: boolean;
}

/** One row of GET /api/traffic/live (app.py:980). */
export interface TrafficLiveCamera {
  camera_id: string;
  at: string;
  total: number;
  cumulative_total: number;
  loops_seen: number;
  counts_by_class: Record<string, number> | null;
  directions: Record<string, number> | null;
  mean_dwell_s: number | null;
}

/** GET /api/traffic/live (app.py:980). */
export interface TrafficLive {
  cameras: TrafficLiveCamera[];
  zone_events: number;
}

/** One learned hourly baseline (netra/analytics/baseline.py `Baseline.as_dict`). */
export interface Baseline {
  camera_id: string;
  hour: number;
  mean: number;
  stdev: number;
  effective_stdev: number;
  samples: number;
  sufficient: boolean;
}

/** GET /api/analytics/baselines (app.py:1032). */
export interface BaselinesResponse {
  buckets_read: number;
  min_samples: number;
  stdev_floor: number;
  hours_learned: number;
  hours_judgeable: number;
  baselines: Baseline[];
}

/** One assessment inside GET /api/analytics/anomalies (netra/analytics/baseline.py `Assessment.as_dict`). */
export interface Anomaly {
  camera_id: string;
  hour: number;
  observed: number;
  status: "insufficient_data" | "stale" | "quiet" | "low" | "normal" | "elevated" | "high" | string;
  z_score: number | null;
  anomalous: boolean;
  explanation: string;
  baseline: Baseline | null;
  bucket_age_s?: number;
}

/** GET /api/analytics/anomalies (app.py:1052). */
export interface AnomaliesResponse {
  buckets_read: number;
  cameras_assessed: number;
  anomalies: number;
  stale: number;
  max_bucket_age_s: number;
  assessments: Anomaly[];
}

/** One finding of GET /api/analytics/cloned-plates (app.py:1122). */
export interface ClonedPlate {
  plate: string;
  sighting_a: {
    detection_id: number;
    camera_id: string;
    camera_name: string | null;
    lat: number | null;
    lon: number | null;
    at: string;
  };
  sighting_b: {
    detection_id: number;
    camera_id: string;
    camera_name: string | null;
    lat: number | null;
    lon: number | null;
    at: string;
  };
  distance_km: number;
  elapsed_s: number;
  implied_kmh: number | null;
  confidence: number;
  reason: string;
}

/** GET /api/analytics/cloned-plates (app.py:1122). */
export interface ClonedPlatesResponse {
  findings: ClonedPlate[];
  count: number;
  min_confidence: number;
  note: string;
}

/** One hop of a mined journey (netra/analytics/loop_index.py `JourneyHop`). */
export interface JourneyHop {
  camera_id: string;
  camera_name: string;
  lat: number | null;
  lon: number | null;
  at: string;
  detection_id: number;
  vehicle_class: string | null;
  colour: string | null;
  plate_text: string | null;
  evidence_path: string | null;
  similarity: number | null;
  leg_km: number | null;
  leg_seconds: number | null;
  implied_kmh: number | null;
  reason: string | null;
}

/** One mined journey (netra/analytics/loop_index.py `Journey.to_dict`). */
export interface Journey {
  time_group: string;
  hops: JourneyHop[];
  hop_count: number;
  cameras: string[];
  total_km: number;
  elapsed_s: number;
  mean_similarity: number;
  confidence: number;
  truncated: boolean;
  note: string;
}

/** GET /api/analytics/journeys (app.py:1157). */
export interface JourneysResponse {
  group: string;
  cameras: string[];
  journeys: Journey[];
  count: number;
  stored: number;
  mined_now: boolean;
  mining_skipped: boolean;
  last_mined_at: string | null;
  next_mine: string;
  mined_at_similarity: number;
  filters_applied: { min_hops: number; min_similarity: number; applied_by: string };
  index: {
    detections_in_group: number;
    with_scene_time: number;
    comparable: number;
    excluded_no_scene_time: number;
    excluded_no_embedding: number;
    note: string;
  };
  note: string;
}

/** GET /api/detections/stats (app.py:396). */
export interface DetectionStats {
  total_detections: number;
  with_plate: number;
  plate_rate_pct: number;
  by_class: Record<string, number>;
  top_cameras: Record<string, number>;
  total_alerts: number;
}

/** GET /api/pipeline/status (app.py:560, netra/pipeline.py:587). */
export interface PipelineStatus {
  running: boolean;
  started_at: string | null;
  inference: {
    submitted: number;
    dropped: number;
    processed: number;
    vehicles: number;
    plates: number;
    embedded: number;
    clocks_anchored: number;
    plate_consensus_applied: number;
    dark_cameras: number;
    dark_frames_skipped: number;
    infer_ms: number;
  };
  queue_depth: number;
  write_queue_depth: number;
  scheduling: {
    escalated: string[];
    escalated_count: number;
    max_escalated: number;
    escalation_denied: number;
  };
  traffic: unknown;
  zone_events: number;
  watchlist_index: {
    entries: number;
    buckets: number;
    window: number;
    unindexed: number;
    largest_bucket: number;
  };
  dark_cameras: string[];
  persistence: Record<string, unknown>;
  attributes: {
    queued: number;
    processed: number;
    dropped: number;
    failed: number;
    broadcast: number;
    enabled: boolean;
    queue_depth: number;
  };
  cameras: CameraHealth[];
}
