export type Unit = "TABLET" | "STRIP" | "BOTTLE" | "BOX" | "UNIT";
export const units: Unit[] = ["STRIP", "TABLET", "BOTTLE", "BOX", "UNIT"];
export interface Medicine {
  medicine_id: string;
  medicine_name: string;
  description: string;
  source_row: number | null;
  source_serial_number: string | null;
  is_active: boolean;
}
export interface Candidate extends Pick<
  Medicine,
  "medicine_id" | "medicine_name" | "description"
> {
  match_type: string;
  ordinal?: number;
}
export interface Match {
  status: string;
  message: string;
  candidates: Candidate[];
}
export interface Item {
  id: number;
  medicine_id: string;
  medicine_name: string;
  description: string;
  quantity: number;
  unit: Unit;
}
export interface Session {
  session_id: string;
  customer_name: string | null;
  customer_phone: string | null;
  status: string;
  created_at: string;
  expires_at: string;
  items: Item[];
}
export interface SessionRow extends Omit<Session, "items"> {
  item_count: number;
  voice_session_id?: string | null;
}
export interface Summary extends Session {
  can_confirm: boolean;
  confirmation_token: string | null;
  summary_digest: string | null;
  message: string;
  line_item_count: number;
}
export interface Order {
  order_id: string;
  session_id: string;
  customer_name: string | null;
  customer_phone: string | null;
  status: string;
  created_at: string;
  call_reference_id: string | null;
  total_line_items: number;
  line_items: {
    id: number;
    medicine_id: string;
    medicine_name: string;
    medicine_description: string;
    quantity: number;
    unit: Unit;
  }[];
}
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
export interface VoiceSession {
  voice_session_id: string;
  order_session_id: string;
  status: string;
  language: "hi" | "en";
  message: string;
}
export interface Turn {
  voice_session_id: string;
  order_session_id: string;
  status: string;
  intent: string;
  language: string;
  transcript?: string | null;
  assistant_text: string;
  candidates: Candidate[];
  order_summary?: { items: Item[]; can_confirm: boolean } | null;
  order: Order | null;
  tts: { audio_base64: string; content_type: string } | null;
  tts_error: string | null;
}
export interface Component {
  provider: string;
  ready: boolean;
  detail: string;
}
export interface VoiceStatus {
  enabled: boolean;
  stt: Component;
  tts: Component;
  llm: Component;
}
export interface Telephony {
  enabled: boolean;
  provider: string;
  configured: boolean;
  simulated: boolean;
  live_verified: boolean;
  live_connected?: boolean;
  inbound_ready?: boolean;
  outbound_ready?: boolean;
  missing_configuration?: string[];
}
export interface Call {
  call_id: string;
  provider: string;
  provider_call_reference: string;
  voice_session_id: string;
  order_session_id: string;
  customer_phone: string | null;
  status: string;
  started_at: string;
  ended_at: string | null;
  disconnect_reason: string | null;
  confirmed_order_id: string | null;
  error_category: string | null;
  last_latency_ms: number | null;
  transcripts?: {
    sequence: number;
    speaker: "CALLER" | "AGENT";
    text: string;
    created_at: string;
  }[];
}
export interface Stats {
  medicines: number;
  orders: number;
  active_sessions: number;
  calls: number;
  active_calls: number;
}
