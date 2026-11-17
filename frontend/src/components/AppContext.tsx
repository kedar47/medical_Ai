import { createContext, useContext, useState, type ReactNode } from "react";
import { Alert, Snackbar } from "@mui/material";
interface Active {
  sessionId: string;
  voiceId?: string;
}
const Context = createContext<{
  active: Active | null;
  setActive: (a: Active | null) => void;
  notify: (text: string, severity?: "success" | "error") => void;
}>({ active: null, setActive: () => {}, notify: () => {} });
export function AppProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState<Active | null>(null);
  const [toast, setToast] = useState<{
    text: string;
    severity: "success" | "error";
  } | null>(null);
  return (
    <Context.Provider
      value={{
        active,
        setActive,
        notify: (text, severity = "success") => setToast({ text, severity }),
      }}
    >
      {children}
      <Snackbar
        open={!!toast}
        autoHideDuration={6000}
        onClose={() => setToast(null)}
      >
        <Alert severity={toast?.severity} onClose={() => setToast(null)}>
          {toast?.text}
        </Alert>
      </Snackbar>
    </Context.Provider>
  );
}
export const useApp = () => useContext(Context);
