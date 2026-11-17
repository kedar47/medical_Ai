import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  FormControlLabel,
  MenuItem,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Mic,
  Square,
  Send,
  Volume2,
  AudioLines,
  Plus,
  CheckCircle2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Turn } from "../types";
import { useApp } from "../components/AppContext";
import { CustomerForm } from "../components/CustomerForm";
import DraftEditor from "../components/DraftEditor";
import {
  Busy,
  ConfirmDialog,
  Failure,
  Header,
  Panel,
  Status,
} from "../components/common";
interface Message {
  role: "user" | "assistant";
  text: string;
  meta?: string;
}
export default function VoiceAssistant() {
  const { active, setActive, notify } = useApp();
  const cache = useQueryClient();
  const [language, setLanguage] = useState("hi");
  const [text, setText] = useState("");
  const [heard, setHeard] = useState("");
  const [heardSent, setHeardSent] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const [last, setLast] = useState<Turn | null>(null);
  const [recording, setRecording] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [permission, setPermission] = useState("Not requested");
  const [audioError, setAudioError] = useState("");
  const [localError, setLocalError] = useState("");
  const [speak, setSpeak] = useState(true);
  const [confirm, setConfirm] = useState(false);
  const [end, setEnd] = useState(false);
  const [busyStart, setBusyStart] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const tracks = useRef<MediaStream | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const mounted = useRef(true);
  const submitting = useRef(false);
  const recordingRef = useRef(false);
  const timeline = useRef<HTMLDivElement | null>(null);
  const heardPanel = useRef<HTMLDivElement | null>(null);
  const id = active?.voiceId;
  const session = useQuery({
    queryKey: ["voice-session", id],
    queryFn: ({ signal }) => api.voiceSession(id!, signal),
    enabled: !!id,
  });
  const start = useMutation({
    mutationFn: async (customer: {
      customer_name: string | null;
      customer_phone: string | null;
    }) => {
      if (id) await api.endVoice(id);
      return api.startVoice({
        ...customer,
        language: language === "en" ? "en" : "hi",
      });
    },
    onSuccess: (s) => {
      setActive({ sessionId: s.order_session_id, voiceId: s.voice_session_id });
      setMessages([{ role: "assistant", text: s.message }]);
      setHeard("");
      setText("");
      setLast(null);
      setLocalError("");
      setAudioError("");
      cache.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (e) => notify(errorMessage(e), "error"),
  });
  const stopPlayback = () => {
    if (player.current) {
      player.current.onended = null;
      player.current.onerror = null;
      player.current.pause();
      player.current.src = "";
      player.current = null;
    }
    setSpeaking(false);
  };
  const play = (turn = last) => {
    stopPlayback();
    if (!turn?.tts) return;
    const audio = new Audio(
      `data:${turn.tts.content_type};base64,${turn.tts.audio_base64}`,
    );
    player.current = audio;
    setAudioError("");
    audio.onended = () => mounted.current && setSpeaking(false);
    audio.onerror = () => {
      if (mounted.current) {
        setAudioError(
          "Audio could not be played. You can review the assistant text.",
        );
        setSpeaking(false);
      }
    };
    setSpeaking(true);
    audio.play().catch(() => {
      if (mounted.current) {
        setSpeaking(false);
        setAudioError(
          "Playback was blocked. Use Play AI response to try again.",
        );
      }
    });
  };
  const cleanupRecording = () => {
    recordingRef.current = false;
    clearTimeout(timer.current);
    if (recorder.current?.state === "recording") {
      recorder.current.onstop = null;
      recorder.current.stop();
    }
    tracks.current?.getTracks().forEach((t) => t.stop());
    tracks.current = null;
    recorder.current = null;
    if (mounted.current) setRecording(false);
  };
  const refresh = () => {
    cache.invalidateQueries({ queryKey: ["session"] });
    cache.invalidateQueries({ queryKey: ["sessions"] });
    cache.invalidateQueries({ queryKey: ["orders"] });
    cache.invalidateQueries({ queryKey: ["stats"] });
    cache.invalidateQueries({ queryKey: ["voice-session", id] });
  };
  const turn = useMutation({
    mutationFn: async (input: { text?: string; blob?: Blob }) => {
      if (!id) throw new Error("Start a conversation first.");
      const lang =
        language === "auto" ? undefined : language === "en" ? "en" : "hi";
      return input.blob
        ? api.audioTurn(id, input.blob, lang)
        : api.turn(id, input.text!, speak, lang);
    },
    onMutate: () => {
      setBusyStart(Date.now());
      setElapsed(0);
      setLocalError("");
      stopPlayback();
    },
    onSuccess: (result, input) => {
      refresh();
      if (!mounted.current) return;
      setLast(result);
      setMessages((m) => [
        ...m,
        ...(input.blob && result.transcript
          ? [{ role: "user" as const, text: result.transcript }]
          : []),
        {
          role: "assistant",
          text: result.assistant_text,
          meta: `${result.intent} · ${result.language}`,
        },
      ]);
      setConfirm(false);
      if (result.status !== "ACTIVE") {
        cleanupRecording();
        notify(
          result.order
            ? `Order ${result.order.order_id} saved.`
            : `Conversation ${result.status.toLowerCase()}.`,
        );
      }
      if (result.tts && speak) play(result);
    },
    onError: (e) => {
      refresh();
      if (!mounted.current) return;
      const message = errorMessage(e);
      setLocalError(message);
      if (message.includes("CONFIRMED")) {
        cleanupRecording();
        setLast((v) => (v ? { ...v, status: "CONFIRMED" } : v));
      }
      notify(message, "error");
    },
    onSettled: () => {
      submitting.current = false;
      if (mounted.current) setBusyStart(0);
    },
  });
  const transcription = useMutation({
    mutationFn: (blob: Blob) =>
      api.transcribe(
        blob,
        language === "auto" ? undefined : language === "en" ? "en" : "hi",
      ),
    onMutate: () => {
      setBusyStart(Date.now());
      setElapsed(0);
      setAudioError("");
      setHeard("");
      setText("");
    },
    onSuccess: (result) => {
      if (!mounted.current) return;
      if (!result.transcript.trim()) {
        setAudioError(
          "No speech was recognized. Please record again or type your request.",
        );
        return;
      }
      setHeardSent(false);
      setHeard(result.transcript);
      setText(result.transcript);
    },
    onError: (error) => {
      if (mounted.current) setAudioError(errorMessage(error));
    },
    onSettled: () => {
      submitting.current = false;
      if (mounted.current) setBusyStart(0);
    },
  });
  const send = (value: string) => {
    if (!value.trim() || submitting.current || !id || !isActive) return;
    submitting.current = true;
    setHeardSent(true);
    setMessages((m) => [...m, { role: "user", text: value }]);
    setText("");
    turn.mutate({ text: value });
  };
  const isActive =
    !!id &&
    session.data?.status === "ACTIVE" &&
    (!last || last.status === "ACTIVE");
  const busy = turn.isPending || start.isPending || transcription.isPending;
  const endMutation = useMutation({
    mutationFn: () => api.endVoice(id!),
    onSuccess: () => {
      cleanupRecording();
      stopPlayback();
      setActive(null);
      setLast(null);
      setEnd(false);
      refresh();
      notify("Conversation ended. An unconfirmed draft was cancelled.");
    },
    onError: (e) => notify(errorMessage(e), "error"),
  });
  async function record() {
    if (!isActive || busy || submitting.current || recordingRef.current) return;
    setAudioError("");
    setHeard("");
    setHeardSent(false);
    setText("");
    setLast((previous) =>
      previous ? { ...previous, order_summary: null } : previous,
    );
    stopPlayback();
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
        throw new Error(
          "This browser does not support microphone recording. Use typed requests instead.",
        );
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!mounted.current) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      tracks.current = stream;
      setPermission("Granted");
      const mime = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ].find((t) => MediaRecorder.isTypeSupported(t));
      if (!mime) {
        stream.getTracks().forEach((t) => t.stop());
        throw new Error(
          "No supported browser audio format. Use typed requests.",
        );
      }
      const current = new MediaRecorder(stream, { mimeType: mime });
      recorder.current = current;
      const chunks: Blob[] = [];
      current.ondataavailable = (e) => {
        if (e.data.size) chunks.push(e.data);
      };
      current.onstop = () => {
        recordingRef.current = false;
        clearTimeout(timer.current);
        stream.getTracks().forEach((t) => t.stop());
        tracks.current = null;
        if (!mounted.current) return;
        setRecording(false);
        const blob = new Blob(chunks, { type: mime });
        if (blob.size && blob.size <= 10_000_000 && !submitting.current) {
          submitting.current = true;
          transcription.mutate(blob);
        } else
          setAudioError(
            "Recording is empty or exceeds 10 MB. Try a shorter recording.",
          );
      };
      current.start();
      recordingRef.current = true;
      setRecording(true);
      timer.current = setTimeout(
        () => current.state === "recording" && current.stop(),
        60000,
      );
    } catch (e) {
      setPermission(
        e instanceof DOMException && e.name === "NotAllowedError"
          ? "Denied"
          : "Unavailable",
      );
      setAudioError(errorMessage(e));
      cleanupRecording();
    }
  }
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      cleanupRecording();
      if (player.current) {
        player.current.onended = null;
        player.current.onerror = null;
        player.current.pause();
        player.current.src = "";
      }
    };
  }, []);
  useEffect(() => {
    timeline.current?.scrollTo({
      top: timeline.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);
  useEffect(() => {
    if (!busyStart) return;
    const t = setInterval(
      () => setElapsed(Math.floor((Date.now() - busyStart) / 1000)),
      1000,
    );
    return () => clearInterval(t);
  }, [busyStart]);
  useEffect(() => {
    if (heard)
      heardPanel.current?.scrollIntoView?.({
        behavior: "smooth",
        block: "nearest",
      });
  }, [heard]);
  const state =
    last?.status === "CONFIRMED" || session.data?.status === "CONFIRMED"
      ? "Confirmed"
      : transcription.isPending
        ? "Transcribing"
        : busy
          ? "Processing"
          : recording
            ? "Recording"
            : speaking
              ? "Speaking"
              : last?.candidates.length
                ? "Waiting for clarification"
                : last?.intent === "show_summary" &&
                    last?.order_summary?.can_confirm
                  ? "Waiting for confirmation"
                  : isActive
                    ? "Listening"
                    : session.data?.status || "Idle";
  return (
    <>
      <Header
        eyebrow="CONVERSATIONS, WITH CONFIDENCE"
        title="AI Voice Assistant"
        description="Speak naturally. Choose exact medicines. Confirm only after reviewing the complete order."
      />
      <Box className="voice-grid">
        <Stack spacing={2}>
          <Panel title="Conversation controls">
            <TextField
              fullWidth
              select
              label="Conversation language"
              value={language}
              disabled={busy || recording}
              onChange={(e) => setLanguage(e.target.value)}
            >
              {[
                ["hi", "Hindi"],
                ["en", "English"],
                ["hinglish", "Hinglish (Hindi setting)"],
                ["auto", "Auto detect (Hindi greeting)"],
              ].map(([v, l]) => (
                <MenuItem key={v} value={v}>
                  {l}
                </MenuItem>
              ))}
            </TextField>
            <Typography
              variant="caption"
              color="text.secondary"
              display="block"
              my={2}
            >
              Hinglish uses the backend Hindi setting. Auto omits the turn
              language override.
            </Typography>
            {!isActive && !busy ? (
              <CustomerForm
                onSubmit={(v) => {
                  cleanupRecording();
                  stopPlayback();
                  start.mutate(v);
                }}
                busy={start.isPending}
                label={id ? "Start New Order" : "Start Conversation"}
              />
            ) : (
              <Stack spacing={2}>
                <Button
                  variant="contained"
                  startIcon={
                    recording ? <Square size={17} /> : <Mic size={17} />
                  }
                  disabled={!isActive || busy}
                  onClick={() =>
                    recording ? recorder.current?.stop() : record()
                  }
                >
                  {recording ? "Stop Recording" : "Start Recording"}
                </Button>
                <Typography variant="caption">
                  Microphone permission: {permission}
                </Typography>
                <Button
                  startIcon={<Volume2 size={17} />}
                  disabled={!last?.tts || busy}
                  onClick={() => play()}
                >
                  Play AI response
                </Button>
                <Button disabled={!speaking} onClick={stopPlayback}>
                  Stop audio playback
                </Button>
                <FormControlLabel
                  control={
                    <Switch
                      checked={speak}
                      onChange={(e) => setSpeak(e.target.checked)}
                    />
                  }
                  label="Speak responses"
                />
                <Button
                  color="error"
                  disabled={busy || recording}
                  onClick={() => setEnd(true)}
                >
                  End Conversation
                </Button>
              </Stack>
            )}
            {start.isError && <Failure error={start.error} />}
          </Panel>
          <Alert severity="info">
            Local speech processing can take 27–41 seconds. Ending is available
            after a pending request finishes; cancelling a browser request
            cannot undo a saved order.
          </Alert>
        </Stack>
        <Panel title="Conversation" action={<Status value={state} />}>
          <Box
            className="conversation"
            ref={timeline}
            role="log"
            aria-label="Conversation timeline"
            aria-live="polite"
          >
            {!messages.length ? (
              <Box className="conversation-empty">
                <AudioLines size={42} strokeWidth={1} />
                <Typography variant="h6" mt={2}>
                  Good care starts with clarity.
                </Typography>
                <Typography color="text.secondary" mt={1}>
                  {isActive
                    ? "Session restored. Previous transcripts are not stored; continue with a new request."
                    : "Start a conversation, then record or type your medicine request."}
                </Typography>
              </Box>
            ) : (
              messages.map((m, i) => (
                <Box className={`bubble ${m.role}`} key={i}>
                  <Typography variant="overline">
                    {m.role === "user" ? "You" : "Medora assistant"}
                  </Typography>
                  <Typography whiteSpace="pre-wrap">{m.text}</Typography>
                  {m.meta && (
                    <Typography variant="caption" color="text.secondary">
                      {m.meta}
                    </Typography>
                  )}
                </Box>
              ))
            )}
            {busy && (
              <Busy
                text={`${transcription.isPending ? "Transcribing your recording" : "AI is processing your request"}${elapsed ? ` · ${elapsed}s` : ""}…`}
              />
            )}
          </Box>
          {localError && (
            <Failure
              error={new Error(localError)}
              retry={() => session.refetch()}
            />
          )}{" "}
          {audioError && (
            <Alert severity="warning" onClose={() => setAudioError("")}>
              {audioError}
            </Alert>
          )}
          {last?.tts_error && (
            <Alert severity="warning">
              Speech unavailable: {last.tts_error}. Review the text response.
            </Alert>
          )}
          {recording && (
            <Alert severity="info" sx={{ mt: 2 }}>
              Recording your voice… Press Stop Recording to see the recognized
              words.
            </Alert>
          )}
          {heard && (
            <Box
              role="region"
              aria-label="Recognized speech"
              ref={heardPanel}
              sx={{
                mt: 2,
                p: 2,
                border: "1px solid #bcded6",
                borderRadius: 2,
                backgroundColor: "#edf7f3",
              }}
            >
              <Typography variant="overline" color="primary">
                You said
              </Typography>
              <Typography
                sx={{ fontSize: 16, fontWeight: 600, whiteSpace: "pre-wrap" }}
              >
                {heard}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                {heardSent
                  ? "Sent to the AI. Your request and the assistant reply appear in the conversation above."
                  : "Check the medicine name and quantity. Edit the text below if needed, then press Send request. Recording alone does not change your order."}
              </Typography>
              {!heardSent && (
                <Button
                  size="small"
                  disabled={busy}
                  onClick={() => {
                    setHeard("");
                    setText("");
                  }}
                >
                  Discard recording
                </Button>
              )}
            </Box>
          )}
          <Stack
            component="form"
            direction="row"
            spacing={1}
            mt={2}
            onSubmit={(e) => {
              e.preventDefault();
              send(text);
            }}
          >
            <TextField
              fullWidth
              size="small"
              label="Type a medicine request"
              multiline
              maxRows={4}
              helperText={
                heard
                  ? "Edit the recognized words here before sending them to the AI."
                  : "Type here, or record your medicine name and quantity."
              }
              value={text}
              onChange={(e) => setText(e.target.value)}
              disabled={!isActive || busy || recording}
            />
            <Button
              type="submit"
              variant="contained"
              aria-label="Send request"
              disabled={!text.trim() || !isActive || busy || recording}
            >
              <Send size={18} />
              <span style={{ marginLeft: 6 }}>Send</span>
            </Button>
          </Stack>
          {last?.candidates.length ? (
            <Box mt={2}>
              {last.candidates.map((c, i) => (
                <Button
                  key={c.medicine_id}
                  disabled={busy || !isActive}
                  onClick={() => send(c.medicine_id)}
                  sx={{ display: "block", textAlign: "left" }}
                >
                  Choose {i + 1}: {c.medicine_name} · {c.description} ·{" "}
                  {c.medicine_id}
                </Button>
              ))}
            </Box>
          ) : null}
          {last?.order && (
            <Alert icon={<CheckCircle2 />} severity="success" sx={{ mt: 2 }}>
              Order saved: {last.order.order_id}.{" "}
              <Link to="/orders">View confirmed orders</Link>
            </Alert>
          )}
        </Panel>
        <Panel
          title="Current order"
          action={
            isActive ? (
              <Button
                component={Link}
                to="/matching"
                size="small"
                startIcon={<Plus size={14} />}
              >
                Add
              </Button>
            ) : undefined
          }
        >
          {id && active ? (
            <>
              <Typography variant="caption" className="reference">
                {active.sessionId}
              </Typography>
              <DraftEditor sessionId={active.sessionId} voiceId={id} />
              <Stack spacing={1} mt={2}>
                <Button
                  variant="outlined"
                  disabled={
                    !isActive || busy || recording || (!!heard && !heardSent)
                  }
                  onClick={() => send("show summary")}
                >
                  Review & speak complete summary
                </Button>
                <Button
                  variant="contained"
                  disabled={
                    !isActive ||
                    busy ||
                    recording ||
                    !last?.order_summary?.can_confirm ||
                    last.intent !== "show_summary" ||
                    !!text.trim()
                  }
                  onClick={() => setConfirm(true)}
                >
                  Explicitly confirm order
                </Button>
              </Stack>
            </>
          ) : (
            <Typography color="text.secondary">
              The backend draft appears when you start a conversation.
            </Typography>
          )}
        </Panel>
      </Box>
      <ConfirmDialog
        open={confirm}
        title="Confirm the complete order?"
        onClose={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          send("yes confirm");
        }}
        busy={busy}
      >
        {last?.order_summary?.items.map((i) => (
          <Typography key={i.id} mb={1}>
            {i.medicine_name} · {i.description} — {i.quantity} {i.unit}
          </Typography>
        ))}
        <Alert severity="info">
          Confirm only after customer approval. The backend revalidates the
          current summary and correction guard before saving.
        </Alert>
      </ConfirmDialog>
      <ConfirmDialog
        open={end}
        title="End this conversation?"
        onClose={() => setEnd(false)}
        onConfirm={() => endMutation.mutate()}
        busy={endMutation.isPending}
      >
        An unconfirmed draft will be cancelled. Saved orders remain preserved.
      </ConfirmDialog>
    </>
  );
}
