import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";
import { api, client, errorMessage } from "../api/client";
import { AppProvider, useApp } from "../components/AppContext";
import App from "../App";
import Dashboard from "../pages/Dashboard";
import Medicines from "../pages/Medicines";
import Matching from "../pages/Matching";
import Sessions from "../pages/Sessions";
import Orders from "../pages/Orders";
import Voice from "../pages/VoiceAssistant";
import Calls from "../pages/Calls";
import SystemStatus from "../pages/SystemStatus";
import DemoTesting from "../pages/DemoTesting";
import DraftEditor from "../components/DraftEditor";
import { CustomerForm } from "../components/CustomerForm";

// Synthetic test fixtures only; the application has no demo-business-data fallback.
const medicine = {
  medicine_id: "MED-TEST",
  medicine_name: "Test medicine",
  description: "200MG test pack",
  source_row: 1,
  source_serial_number: "1",
  is_active: true,
};
const item = { id: 1, ...medicine, quantity: 2, unit: "STRIP" as const };
const session = {
  session_id: "session_test",
  customer_name: null,
  customer_phone: null,
  status: "ACTIVE",
  created_at: new Date().toISOString(),
  expires_at: new Date(Date.now() + 600000).toISOString(),
  items: [item],
};
const order = {
  order_id: "order_test",
  session_id: session.session_id,
  customer_name: null,
  customer_phone: null,
  status: "PENDING_PHARMACY_REVIEW",
  created_at: session.created_at,
  call_reference_id: null,
  total_line_items: 1,
  line_items: [{ ...item, medicine_description: item.description }],
};
const voiceSession = {
  voice_session_id: "voice_test",
  order_session_id: session.session_id,
  status: "ACTIVE",
  language: "en" as const,
  message: "Which medicine would you like?",
};
const turn = {
  voice_session_id: "voice_test",
  order_session_id: "session_test",
  status: "ACTIVE",
  intent: "add_item",
  language: "en",
  assistant_text: "Added your exact medicine.",
  candidates: [],
  order_summary: { items: [item], can_confirm: false },
  order: null,
  tts: null,
  tts_error: null,
};
const voiceStatus = {
  enabled: true,
  stt: { provider: "faster-whisper", ready: true, detail: "Configured" },
  tts: { provider: "kokoro-onnx", ready: true, detail: "Configured" },
  llm: {
    provider: "ollama",
    ready: false,
    detail: "Optional extraction is disabled.",
  },
};
function page<T>(items: T[]) {
  return { items, total: items.length, offset: 0, limit: 10 };
}
beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "health").mockResolvedValue({
    status: "ok",
    database: "connected",
  });
  vi.spyOn(api, "stats").mockResolvedValue({
    medicines: 20,
    orders: 0,
    active_sessions: 1,
    calls: 0,
    active_calls: 0,
  });
  vi.spyOn(api, "orders").mockResolvedValue(page([]));
  vi.spyOn(api, "medicines").mockResolvedValue(page([medicine]));
  vi.spyOn(api, "sessions").mockResolvedValue(
    page([{ ...session, item_count: 1 }]),
  );
  vi.spyOn(api, "session").mockResolvedValue(session);
  vi.spyOn(api, "voiceStatus").mockResolvedValue(voiceStatus);
  vi.spyOn(api, "telephony").mockResolvedValue({
    enabled: false,
    provider: "exotel",
    configured: false,
    simulated: false,
    live_verified: false,
  });
  vi.spyOn(api, "calls").mockResolvedValue(page([]));
  vi.spyOn(api, "voiceSession").mockResolvedValue(voiceSession);
  vi.spyOn(api, "startVoice").mockResolvedValue(voiceSession);
  vi.spyOn(api, "turn").mockResolvedValue(turn);
  vi.spyOn(api, "endVoice").mockResolvedValue({
    ...voiceSession,
    status: "ENDED",
  });
  vi.spyOn(api, "summary").mockResolvedValue({
    ...session,
    can_confirm: true,
    confirmation_token: "server-token-123456789",
    summary_digest: "digest",
    message: "Review the order",
    line_item_count: 1,
  });
  vi.spyOn(api, "confirm").mockResolvedValue(order);
});
afterEach(() => vi.unstubAllGlobals());
function Active({ voice = false }: { voice?: boolean }) {
  const { setActive } = useApp();
  return (
    <button
      onClick={() =>
        setActive({
          sessionId: "session_test",
          ...(voice ? { voiceId: "voice_test" } : {}),
        })
      }
    >
      Select active test session
    </button>
  );
}
function mount(
  ui: ReactNode,
  { active = false, voice = false }: { active?: boolean; voice?: boolean } = {},
) {
  const query = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={query}>
      <MemoryRouter>
        <AppProvider>
          {active && <Active voice={voice} />} {ui}
        </AppProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const user = () => userEvent.setup();

test("all ten navigation destinations exist and navigation opens the catalogue", async () => {
  mount(<App />);
  const nav = screen.getByRole("navigation", { name: "Primary navigation" });
  expect(within(nav).getAllByRole("link")).toHaveLength(10);
  await user().click(
    within(nav).getByRole("link", { name: "Medicine Catalogue" }),
  );
  expect(
    await screen.findByRole("heading", { name: "Your approved catalogue" }),
  ).toBeVisible();
});
test("overview displays actual aggregate response without deriving totals from a page", async () => {
  mount(<Dashboard />);
  expect(await screen.findByText("20")).toBeVisible();
  expect(api.stats).toHaveBeenCalled();
  expect(
    await screen
      .findByText("No records yet.", { exact: false })
      .catch(() => null),
  ).toBeNull();
  expect(
    screen.getByText(
      "Confirmed orders will appear here after customer approval.",
    ),
  ).toBeVisible();
});
test("catalogue loading and details preserve exact variant", async () => {
  mount(<Medicines />);
  expect(await screen.findByText("200MG test pack")).toBeVisible();
  await user().click(screen.getByRole("button", { name: "View MED-TEST" }));
  expect(screen.getByText("Original serial: 1")).toBeVisible();
});
test("catalogue search passes query to backend after debounce", async () => {
  mount(<Medicines />);
  await user().type(screen.getByLabelText("Search medicines"), "BRUFEN");
  await waitFor(() =>
    expect(api.medicines).toHaveBeenLastCalledWith(
      "BRUFEN",
      0,
      expect.anything(),
    ),
  );
});
test("matching presents ambiguous variants without auto selection", async () => {
  vi.spyOn(api, "match").mockResolvedValue({
    status: "CLARIFICATION_REQUIRED",
    message: "Choose a variant",
    candidates: [
      { ...medicine, match_type: "EXACT_NAME" },
      {
        ...medicine,
        medicine_id: "MED-TEST2",
        description: "400MG test pack",
        match_type: "EXACT_NAME",
      },
    ],
  });
  mount(<Matching />);
  await user().type(screen.getByLabelText("Medicine name or alias"), "BRUFEN");
  await user().click(screen.getByRole("button", { name: "Find medicine" }));
  expect(await screen.findByText("CLARIFICATION REQUIRED")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Choose MED-TEST" }),
  ).toBeDisabled();
  expect(screen.getByText("400MG test pack")).toBeVisible();
});
test("invalid phone is rejected before request", async () => {
  const submit = vi.fn();
  mount(<CustomerForm busy={false} onSubmit={submit} />);
  await user().type(screen.getByLabelText("Phone (optional, E.164)"), "123");
  await user().click(screen.getByRole("button", { name: "Create session" }));
  expect(await screen.findByText(/Use E.164 format/)).toBeVisible();
  expect(submit).not.toHaveBeenCalled();
});
test("creates a fresh backend order session with optional empty fields", async () => {
  const create = vi.spyOn(api, "createSession").mockResolvedValue(session);
  mount(<Sessions />);
  await user().click(screen.getByRole("button", { name: "New order session" }));
  await user().click(screen.getByRole("button", { name: "Create session" }));
  await waitFor(() =>
    expect(create).toHaveBeenCalledWith(
      { customer_name: null, customer_phone: null },
      expect.anything(),
    ),
  );
});
test("explicit catalogue choice adds medicine through API", async () => {
  vi.spyOn(api, "match").mockResolvedValue({
    status: "EXACT_MATCH",
    message: "Exact",
    candidates: [{ ...medicine, match_type: "EXACT_NAME" }],
  });
  const add = vi.spyOn(api, "addItem").mockResolvedValue(session);
  mount(<Matching />, { active: true });
  await user().click(screen.getByText("Select active test session"));
  await user().type(
    screen.getByLabelText("Medicine name or alias"),
    "Test medicine",
  );
  await user().click(screen.getByText("Find medicine"));
  await user().click(
    await screen.findByRole("button", { name: "Choose MED-TEST" }),
  );
  await user().click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(add).toHaveBeenCalledWith("session_test", "MED-TEST", 1, "STRIP"),
  );
});
test("quantity update uses existing draft mutation and refreshes server state", async () => {
  const update = vi.spyOn(api, "updateItem").mockResolvedValue(session);
  mount(<DraftEditor sessionId="session_test" />);
  await user().click(
    await screen.findByRole("button", { name: "Edit Test medicine" }),
  );
  const quantity = screen.getByLabelText("New quantity");
  await user().clear(quantity);
  await user().type(quantity, "3");
  await user().click(screen.getByRole("button", { name: "Save change" }));
  await waitFor(() =>
    expect(update).toHaveBeenCalledWith("session_test", 1, 3, "STRIP"),
  );
});
test("removal requires confirmation", async () => {
  const remove = vi.spyOn(api, "removeItem").mockResolvedValue(session);
  mount(<DraftEditor sessionId="session_test" />);
  await user().click(
    await screen.findByRole("button", { name: "Remove Test medicine" }),
  );
  expect(remove).not.toHaveBeenCalled();
  await user().click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(remove).toHaveBeenCalledWith("session_test", 1));
});
test("fresh summary and explicit consent submit server-issued token", async () => {
  mount(<DraftEditor sessionId="session_test" />);
  await user().click(await screen.findByText("Review complete order"));
  expect(await screen.findByText("Review every medicine")).toBeVisible();
  expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled();
  await user().click(screen.getByRole("checkbox"));
  await user().click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(api.confirm).toHaveBeenCalledWith(
      "session_test",
      "server-token-123456789",
    ),
  );
  expect(
    await screen.findByText("Order saved for pharmacy review."),
  ).toBeVisible();
});
test("stale token error discards summary instead of retrying confirmation", async () => {
  vi.mocked(api.confirm).mockRejectedValue(
    new Error("Stale token. Review again."),
  );
  mount(<DraftEditor sessionId="session_test" />);
  await user().click(await screen.findByText("Review complete order"));
  await user().click(await screen.findByRole("checkbox"));
  await user().click(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() =>
    expect(screen.queryByText("Review every medicine")).not.toBeInTheDocument(),
  );
  expect(api.confirm).toHaveBeenCalledTimes(1);
});
test("confirmed draft cannot be edited", async () => {
  vi.mocked(api.session).mockResolvedValue({ ...session, status: "CONFIRMED" });
  mount(<DraftEditor sessionId="session_test" />);
  expect(
    await screen.findByRole("button", { name: "Edit Test medicine" }),
  ).toBeDisabled();
});
test("orders show real returned rows and details", async () => {
  vi.mocked(api.orders).mockResolvedValue(page([order]));
  vi.spyOn(api, "order").mockResolvedValue(order);
  mount(<Orders />);
  await user().click(await screen.findByRole("button", { name: "View order" }));
  expect(await screen.findByText("Order details")).toBeVisible();
  expect(screen.getByText("200MG test pack")).toBeVisible();
});
test("voice start sends real language setting", async () => {
  mount(<Voice />);
  await user().click(
    screen.getByRole("button", { name: "Start Conversation" }),
  );
  await waitFor(() =>
    expect(api.startVoice).toHaveBeenCalledWith({
      customer_name: null,
      customer_phone: null,
      language: "hi",
    }),
  );
  expect(
    await screen.findByRole("button", { name: "Start Recording" }),
  ).toBeEnabled();
});
test("voice turns display AI text and intent", async () => {
  mount(<Voice />, { active: true, voice: true });
  await user().click(screen.getByText("Select active test session"));
  await waitFor(() =>
    expect(screen.getByLabelText("Type a medicine request")).toBeEnabled(),
  );
  await user().type(
    screen.getByLabelText("Type a medicine request"),
    "add one strip",
  );
  await user().click(screen.getByRole("button", { name: "Send request" }));
  expect(await screen.findByText("Added your exact medicine.")).toBeVisible();
  expect(screen.getByText("add_item · en")).toBeVisible();
});
test("confirmed voice response stops input and offers Start New Order", async () => {
  vi.mocked(api.turn).mockResolvedValue({
    ...turn,
    status: "CONFIRMED",
    order,
  });
  mount(<Voice />, { active: true, voice: true });
  await user().click(screen.getByText("Select active test session"));
  await waitFor(() =>
    expect(screen.getByLabelText("Type a medicine request")).toBeEnabled(),
  );
  await user().type(
    screen.getByLabelText("Type a medicine request"),
    "yes confirm",
  );
  await user().click(screen.getByRole("button", { name: "Send request" }));
  expect(
    await screen.findByRole("button", { name: "Start New Order" }),
  ).toBeEnabled();
  expect(screen.getByLabelText("Type a medicine request")).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Start Recording" }),
  ).not.toBeInTheDocument();
  await user().click(screen.getByRole("button", { name: "Start New Order" }));
  await waitFor(() => expect(api.startVoice).toHaveBeenCalled());
  expect(api.endVoice).toHaveBeenCalledWith("voice_test");
});
test("microphone denial shows a readable error and keeps typed fallback", async () => {
  Object.defineProperty(navigator, "mediaDevices", {
    value: {
      getUserMedia: vi
        .fn()
        .mockRejectedValue(
          new DOMException("Microphone denied", "NotAllowedError"),
        ),
    },
    configurable: true,
  });
  vi.stubGlobal("MediaRecorder", class {});
  mount(<Voice />, { active: true, voice: true });
  await user().click(screen.getByText("Select active test session"));
  await user().click(
    await screen.findByRole("button", { name: "Start Recording" }),
  );
  expect(await screen.findByText("Microphone denied")).toBeVisible();
  expect(screen.getByLabelText("Type a medicine request")).toBeEnabled();
});
test("AI reply audio can play and stop", async () => {
  const play = vi.fn().mockResolvedValue(undefined);
  const pause = vi.fn();
  vi.stubGlobal(
    "Audio",
    class {
      src = "";
      onended = null;
      onerror = null;
      play = play;
      pause = pause;
    },
  );
  vi.mocked(api.turn).mockResolvedValue({
    ...turn,
    tts: { audio_base64: "AAAA", content_type: "audio/wav" },
  });
  mount(<Voice />, { active: true, voice: true });
  await user().click(screen.getByText("Select active test session"));
  await waitFor(() =>
    expect(screen.getByLabelText("Type a medicine request")).toBeEnabled(),
  );
  await user().type(screen.getByLabelText("Type a medicine request"), "hello");
  await user().click(screen.getByRole("button", { name: "Send request" }));
  await waitFor(() => expect(play).toHaveBeenCalled());
  await user().click(
    screen.getByRole("button", { name: "Stop audio playback" }),
  );
  expect(pause).toHaveBeenCalled();
});
test("backend error exposes retry instead of fake data", async () => {
  vi.mocked(api.stats).mockRejectedValue(new Error("Database unavailable"));
  mount(<Dashboard />);
  expect(await screen.findByText("Database unavailable")).toBeVisible();
  expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
});
test("live calls accurately display disabled state", async () => {
  mount(<Calls live />);
  expect(
    await screen.findByText(
      "Telephony is not configured. Live incoming calls are unavailable.",
    ),
  ).toBeVisible();
  expect(
    await screen.findByText("There are no active call records."),
  ).toBeVisible();
});
test("live dial stays disabled until outbound configuration is ready", async () => {
  vi.mocked(api.telephony).mockResolvedValue({
    enabled: true,
    provider: "exotel",
    configured: true,
    simulated: false,
    live_verified: false,
    outbound_ready: false,
    missing_configuration: ["EXOTEL_API_KEY"],
  });
  mount(<Calls live />);
  expect(screen.getByRole("button", { name: "Dial now" })).toBeDisabled();
  expect(await screen.findByText(/EXOTEL_API_KEY/)).toBeVisible();
});
test("live dial passes an E.164 destination to the backend", async () => {
  const dial = vi.spyOn(api, "dialCall").mockResolvedValue({
    ...session,
    call_id: "call_live",
    provider: "exotel",
    provider_call_reference: "exotel-live",
    voice_session_id: "voice_live",
    order_session_id: "session_live",
    customer_phone: "+919876543210",
    status: "RINGING",
    started_at: session.created_at,
    ended_at: null,
    disconnect_reason: null,
    confirmed_order_id: null,
    error_category: null,
    last_latency_ms: null,
  } as never);
  vi.mocked(api.telephony).mockResolvedValue({
    enabled: true,
    provider: "exotel",
    configured: true,
    simulated: false,
    live_verified: false,
    outbound_ready: true,
    missing_configuration: [],
  });
  mount(<Calls live />);
  await user().type(
    screen.getByLabelText("Destination (E.164)"),
    "+919876543210",
  );
  await user().click(await screen.findByRole("button", { name: "Dial now" }));
  await waitFor(() =>
    expect(dial).toHaveBeenCalledWith("+919876543210", "hi"),
  );
});
test("empty call history does not manufacture calls", async () => {
  mount(<Calls />);
  expect(
    await screen.findByText(
      "No provider or simulated calls have been recorded.",
    ),
  ).toBeVisible();
});
test("mock call rows are explicitly identified", async () => {
  vi.mocked(api.calls).mockResolvedValue(
    page([
      {
        call_id: "call_test",
        provider: "mock",
        provider_call_reference: "simulated-fixture",
        voice_session_id: "voice_test",
        order_session_id: "session_test",
        customer_phone: null,
        status: "DISCONNECTED",
        started_at: session.created_at,
        ended_at: session.created_at,
        disconnect_reason: "test",
        confirmed_order_id: null,
        error_category: null,
        last_latency_ms: null,
      },
    ]),
  );
  mount(<Calls />);
  expect(await screen.findByText("MOCK / SIMULATION")).toBeVisible();
  expect(screen.getByText("simulated-fixture")).toBeVisible();
});
test("status uses backend component flags", async () => {
  mount(<SystemStatus />);
  expect(await screen.findByText("faster-whisper")).toBeVisible();
  expect(screen.getByText("Optional extraction is disabled.")).toBeVisible();
  expect(
    screen.getByText(
      "A real two-way telephone call is required before this changes to Verified.",
    ),
  ).toBeVisible();
});
test("manual testing checklist clearly labels evidence and blocked phone tests", async () => {
  mount(<DemoTesting />);
  expect(screen.getByText(/These are manual notes/)).toBeVisible();
  expect(
    screen.getByText(/Blocked — no successful real two-way telephone call/),
  ).toBeVisible();
  const select = screen.getByLabelText("Result: Single medicine order");
  await user().click(select);
  await user().click(screen.getByRole("option", { name: "Passed" }));
  expect(select).toHaveTextContent("Passed");
});
test("API summary validates critical response structure", async () => {
  vi.mocked(api.summary).mockRestore();
  vi.spyOn(client, "get").mockResolvedValue({ data: { can_confirm: true } });
  await expect(api.summary("session_test")).rejects.toThrow();
});
test("API failures have safe readable fallback", () => {
  expect(errorMessage(new Error("Expired session"))).toBe("Expired session");
  expect(errorMessage(null)).toBe("The request failed. Please retry.");
});

test("recorded speech is visible and editable before AI submission, and microphone tracks release", async () => {
  const stop = vi.fn();
  Object.defineProperty(navigator, "mediaDevices", {
    value: {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [{ stop }] }),
    },
    configurable: true,
  });
  class Recorder {
    static isTypeSupported() {
      return true;
    }
    state = "inactive";
    ondataavailable: ((e: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    start() {
      this.state = "recording";
    }
    stop() {
      this.state = "inactive";
      this.ondataavailable?.({
        data: new Blob(["sample"], { type: "audio/webm" }),
      });
      this.onstop?.();
    }
  }
  vi.stubGlobal("MediaRecorder", Recorder);
  const upload = vi
    .spyOn(api, "transcribe")
    .mockResolvedValue({ transcript: "Brufen 400 two strips", language: "en" });
  mount(<Voice />, { active: true, voice: true });
  await user().click(screen.getByText("Select active test session"));
  await user().click(
    await screen.findByRole("button", { name: "Start Recording" }),
  );
  await user().click(
    await screen.findByRole("button", { name: "Stop Recording" }),
  );
  expect(
    await screen.findByRole("region", { name: "Recognized speech" }),
  ).toHaveTextContent("Brufen 400 two strips");
  expect(upload).toHaveBeenCalledWith(expect.any(Blob), "hi");
  expect(api.turn).not.toHaveBeenCalled();
  const review = screen.getByLabelText("Type a medicine request");
  expect(review).toHaveValue("Brufen 400 two strips");
  await user().clear(review);
  await user().type(review, "add three strips of brufen 400");
  await user().click(screen.getByRole("button", { name: "Send request" }));
  await waitFor(() =>
    expect(api.turn).toHaveBeenCalledWith(
      "voice_test",
      "add three strips of brufen 400",
      true,
      "hi",
    ),
  );
  expect(await screen.findByText("Added your exact medicine.")).toBeVisible();
  expect(stop).toHaveBeenCalled();
});
test("confirmation control stays disabled after add until a full summary response", async () => {
  mount(<Voice />, { active: true, voice: true });
  await user().click(screen.getByText("Select active test session"));
  await waitFor(() =>
    expect(screen.getByLabelText("Type a medicine request")).toBeEnabled(),
  );
  vi.mocked(api.turn).mockResolvedValue({
    ...turn,
    order_summary: { items: [item], can_confirm: true },
  });
  await user().type(
    screen.getByLabelText("Type a medicine request"),
    "add two strips",
  );
  await user().click(screen.getByRole("button", { name: "Send request" }));
  await screen.findByText("Added your exact medicine.");
  expect(
    screen.getByRole("button", { name: "Explicitly confirm order" }),
  ).toBeDisabled();
  vi.mocked(api.turn).mockResolvedValue({
    ...turn,
    intent: "show_summary",
    order_summary: { items: [item], can_confirm: true },
  });
  await user().click(
    screen.getByRole("button", { name: "Review & speak complete summary" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Explicitly confirm order" }),
    ).toBeEnabled(),
  );
});
test("voice-associated draft updates preserve conversation guard", async () => {
  mount(<DraftEditor sessionId="session_test" voiceId="voice_test" />);
  await user().click(
    await screen.findByRole("button", { name: "Edit Test medicine" }),
  );
  await user().click(screen.getByRole("button", { name: "Save change" }));
  await waitFor(() =>
    expect(api.turn).toHaveBeenCalledWith(
      "voice_test",
      "change MED-TEST quantity to 2 strip",
      false,
    ),
  );
  expect(api.summary).not.toHaveBeenCalled();
});

test("transcription API sends recording to the existing transcription-only endpoint", async () => {
  const request = vi
    .spyOn(client, "post")
    .mockResolvedValue({ data: { transcript: "two strips", language: "en" } });
  const blob = new Blob(["sample"], { type: "audio/webm" });
  const response = await api.transcribe(blob, "en");
  expect(response.transcript).toBe("two strips");
  expect(request).toHaveBeenCalledWith(
    "/voice/transcribe",
    expect.any(FormData),
  );
  const data = request.mock.calls[0][1] as FormData;
  expect(data.get("language")).toBe("en");
  expect(data.get("file")).toBeInstanceOf(Blob);
});
