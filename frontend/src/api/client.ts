import axios from "axios";
import { z } from "zod";
import type {
  Call,
  Candidate,
  Match,
  Medicine,
  Order,
  Page,
  Session,
  SessionRow,
  Stats,
  Summary,
  Telephony,
  Unit,
  VoiceSession,
  VoiceStatus,
  Turn,
} from "../types";
export const client = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || "/api",
  timeout: 180000,
});
export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (Array.isArray(detail))
      return detail
        .map((d) => `${d.loc?.slice(1).join(" ")}: ${d.msg}`)
        .join(". ");
    if (typeof detail === "string") return detail;
    if (!error.response)
      return "The backend could not be reached. Check the API connection and retry.";
  }
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    typeof error.message === "string"
  )
    return error.message;
  return "The request failed. Please retry.";
}
const get = async <T>(url: string, signal?: AbortSignal) =>
  (await client.get<T>(url, { signal })).data;
const post = async <T>(url: string, data: unknown = {}) =>
  (await client.post<T>(url, data)).data;
const summarySchema = z
  .object({
    confirmation_token: z.string().nullable(),
    summary_digest: z.string().nullable(),
    can_confirm: z.boolean(),
  })
  .passthrough();
export const api = {
  health: (signal?: AbortSignal) =>
    get<{ status: string; database: string }>("/health", signal),
  stats: (signal?: AbortSignal) => get<Stats>("/dashboard/stats", signal),
  medicines: (q = "", offset = 0, signal?: AbortSignal) =>
    get<Page<Medicine>>(
      `/medicines?q=${encodeURIComponent(q)}&limit=10&offset=${offset}`,
      signal,
    ),
  medicine: (id: string, signal?: AbortSignal) =>
    get<Medicine>(`/medicines/${encodeURIComponent(id)}`, signal),
  match: (text: string) => post<Match>("/medicine-matching/search", { text }),
  sessions: (offset = 0, status = "", signal?: AbortSignal) =>
    get<Page<SessionRow>>(
      `/dashboard/sessions?limit=10&offset=${offset}&status=${status}`,
      signal,
    ),
  session: (id: string, signal?: AbortSignal) =>
    get<Session>(`/order-sessions/${id}`, signal),
  createSession: (data: {
    customer_name?: string | null;
    customer_phone?: string | null;
  }) => post<Session>("/order-sessions", data),
  cancelSession: (id: string) => post<Session>(`/order-sessions/${id}/cancel`),
  addItem: (id: string, medicine_id: string, quantity: number, unit: Unit) =>
    post<Session>(`/order-sessions/${id}/items`, {
      medicine_id,
      quantity,
      unit,
    }),
  updateItem: async (
    id: string,
    itemId: number,
    quantity: number,
    unit: Unit,
  ) =>
    (
      await client.patch<Session>(`/order-sessions/${id}/items/${itemId}`, {
        quantity,
        unit,
      })
    ).data,
  removeItem: async (id: string, itemId: number) =>
    (await client.delete<Session>(`/order-sessions/${id}/items/${itemId}`))
      .data,
  summary: async (id: string) => {
    const data = await get<Summary>(`/order-sessions/${id}/summary`);
    summarySchema.parse(data);
    return data;
  },
  confirm: (id: string, confirmation_token: string) =>
    post<Order>(`/order-sessions/${id}/confirm`, {
      confirmation_token,
      explicit_confirmation: true,
      confirmation_source: "REACT_DASHBOARD",
    }),
  orders: (offset = 0, signal?: AbortSignal) =>
    get<Page<Order>>(`/dashboard/orders?limit=10&offset=${offset}`, signal),
  order: (id: string, signal?: AbortSignal) =>
    get<Order>(`/orders/${encodeURIComponent(id)}`, signal),
  voiceStatus: (signal?: AbortSignal) =>
    get<VoiceStatus>("/voice/status", signal),
  startVoice: (data: {
    customer_name?: string | null;
    customer_phone?: string | null;
    language: "hi" | "en";
  }) => post<VoiceSession>("/voice/sessions", data),
  voiceSession: (id: string, signal?: AbortSignal) =>
    get<VoiceSession>(`/voice/sessions/${id}`, signal),
  turn: (id: string, text: string, speak = true, language?: "hi" | "en") =>
    post<Turn>(`/voice/sessions/${id}/turns`, {
      text,
      speak,
      ...(language ? { language } : {}),
    }),
  transcribe: async (blob: Blob, language?: "hi" | "en") => {
    const data = new FormData();
    const extension = blob.type.includes("mp4")
      ? "mp4"
      : blob.type.includes("ogg")
        ? "ogg"
        : "webm";
    data.append("file", blob, `recording.${extension}`);
    if (language) data.append("language", language);
    return post<{ transcript: string; language: string }>(
      "/voice/transcribe",
      data,
    );
  },
  audioTurn: async (id: string, blob: Blob, language?: "hi" | "en") => {
    const data = new FormData();
    const extension = blob.type.includes("mp4")
      ? "mp4"
      : blob.type.includes("ogg")
        ? "ogg"
        : "webm";
    data.append("file", blob, `recording.${extension}`);
    data.append("speak", "true");
    if (language) data.append("language", language);
    return post<Turn>(`/voice/sessions/${id}/audio-turns`, data);
  },
  endVoice: (id: string) => post<VoiceSession>(`/voice/sessions/${id}/end`),
  telephony: (signal?: AbortSignal) =>
    get<Telephony>("/telephony/status", signal),
  calls: (offset = 0, status = "", active = false, signal?: AbortSignal) =>
    get<Page<Call>>(
      `/dashboard/calls?limit=10&offset=${offset}&status=${status}&active=${active}`,
      signal,
    ),
  call: (id: string, signal?: AbortSignal) =>
    get<Call>(`/dashboard/calls/${id}`, signal),
  dialCall: (customer_phone: string, language: "hi" | "en") =>
    post<Call>("/dashboard/calls/outbound", { customer_phone, language }),
  mock: (call_reference: string) =>
    post<{ call_id: string; simulated: boolean; status: string }>(
      "/telephony/mock/incoming",
      { call_reference },
    ),
};
export type { Candidate };
