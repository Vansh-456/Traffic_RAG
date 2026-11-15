export type Lang = "en" | "hi";

export interface Citation {
  id: number;
  kind: "chunk" | "tool";
  title: string;
  source: string;
  url: string | null;
  published_at: string | null;
  snippet: string;
  chunk_id: number | null;
  also_reported_by: number;
}

export interface Card {
  type: "live_traffic" | "weather" | "incident";
  title: string;
  citation: number;
  as_of?: string;
  current_speed?: number;
  free_flow_speed?: number;
  level?: string;
  trend?: string | null;
  conditions?: string;
  temperature_c?: number | null;
  rain_mm?: number | null;
  visibility_m?: number | null;
  flags?: string[];
  description?: string;
  delay_min?: number | null;
}

export interface GeoFeature {
  type: "Feature";
  geometry: { type: string; coordinates: unknown };
  properties: Record<string, unknown>;
}

export interface FeatureCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
}

export interface Internals {
  features?: Record<string, boolean>;
  cache?: { hit: boolean; bucket: string; best_similarity: number; threshold: number };
  query_understanding?: {
    original: string;
    standalone: string;
    language: string;
    intent: string;
    locations: {
      name: string;
      lat: number;
      lon: number;
      method: string;
      confidence: number;
      radius_m: number;
      mention: string;
    }[];
    time_window: { start: string; end: string; label: string; is_live: boolean };
    rewritten_queries: string[];
    sub_questions: string[];
    method: string;
  };
  tools?: {
    name: string;
    args: Record<string, unknown>;
    origin: string;
    latency_ms: number;
    ok: boolean;
    summary: string;
  }[];
  llm_selected_tools?: string[];
  retrieval?: {
    filters: Record<string, unknown>;
    list_sizes: Record<string, number>;
    lists: Record<string, number[]>;
    fused: number;
    reranked: number;
    reranker: string;
    embedder: string;
    toggles: Record<string, boolean>;
    timings_ms: Record<string, number>;
    candidates: CandidateTrace[];
    dropped: (CandidateTrace & { reason: string })[];
  } | null;
  relevance_gate?: { best_score: number; threshold: number; tool_data: boolean; abstain: boolean };
  context?: { evidence_items: number; approx_tokens: number; abstain_path: boolean };
  generation?: { by: string };
  verification?: {
    method: string;
    faithfulness: number;
    changed: boolean;
    claims: { index: number; sentence: string; citations: number[]; supported: boolean; reason: string }[];
  };
  regenerated?: boolean;
  latency_ms?: Record<string, number | null>;
  usage?: {
    calls: {
      model: string;
      purpose: string;
      prompt_tokens: number;
      completion_tokens: number;
      latency_ms: number;
      cost_usd: number;
    }[];
    total_tokens: number;
    cost_usd: number;
  };
  langfuse_trace_url?: string;
}

export interface CandidateTrace {
  chunk_id: number;
  title: string;
  source: string;
  trust_level: string;
  ranks: Record<string, number>;
  rrf: number;
  rerank: number;
  freshness: number;
  trust: number;
  proximity: number;
  final: number;
  distance_m: number | null;
  matched_question: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  status?: string;
  citations?: Citation[];
  cards?: Card[];
  mapFeatures?: FeatureCollection;
  internals?: Internals;
  followUps?: string[];
  verified?: boolean;
  revised?: boolean;
  abstained?: boolean;
  cached?: boolean;
  done?: boolean;
  error?: string;
  rating?: 1 | -1;
}

export interface Advisory {
  chunk_id: number;
  document_id: number;
  title: string;
  url: string;
  source_id: string;
  authority: string | null;
  publisher: string | null;
  language: string;
  linked_document_id: number | null;
  trust_level: "official" | "news" | "sensor";
  event_type: string | null;
  severity: "low" | "medium" | "high" | null;
  valid_from: string | null;
  valid_to: string | null;
  published_at: string | null;
  summary_en: string | null;
  summary_hi: string | null;
  snippet: string;
  geometry: GeoFeature["geometry"] | null;
  also_reported_by: number;
}

export interface Place {
  id: number | null;
  name: string;
  name_hi: string | null;
  type: string;
  lat: number;
  lon: number;
}

export interface SavedRoute {
  id: number;
  name: string;
  origin: { label: string; lat: number; lon: number };
  destination: { label: string; lat: number; lon: number };
  depart_time: string | null;
  days_of_week: number[];
  channels: ("push" | "email" | "inapp")[];
  digest_time: string | null;
  active: boolean;
}

export interface LiveMap {
  as_of: string;
  segments: FeatureCollection;
  incidents: FeatureCollection;
  advisories: FeatureCollection;
}
