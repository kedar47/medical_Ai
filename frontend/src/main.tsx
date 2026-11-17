import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTheme, CssBaseline, ThemeProvider } from "@mui/material";
import App from "./App";
import { AppProvider } from "./components/AppContext";
import "./styles.css";
const theme = createTheme({
  palette: {
    primary: { main: "#14665d", dark: "#104c46" },
    secondary: { main: "#183e4a" },
    background: { default: "#f6f8fa" },
    text: { primary: "#18323d", secondary: "#677b84" },
    success: { main: "#267251" },
  },
  typography: {
    fontFamily: '"Segoe UI", Inter, Arial, sans-serif',
    h3: { fontWeight: 650, fontSize: "2.25rem" },
    h4: { fontWeight: 650, fontSize: "1.85rem", letterSpacing: "-.8px" },
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600, fontSize: "1rem" },
    button: { textTransform: "none", fontWeight: 600 },
  },
  shape: { borderRadius: 10 },
  components: {
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: { root: { minHeight: 38 } },
    },
    MuiPaper: { styleOverrides: { outlined: { borderColor: "#e3e9ec" } } },
    MuiTableCell: {
      styleOverrides: {
        head: {
          background: "#f8fafb",
          color: "#6a7d86",
          fontSize: 11,
          fontWeight: 650,
          textTransform: "uppercase",
          letterSpacing: ".5px",
        },
        root: { padding: "15px 16px", borderColor: "#edf0f2" },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontSize: 10,
          fontWeight: 650,
          borderRadius: 6,
          maxWidth: "100%",
          height: 25,
        },
      },
    },
    MuiTextField: { defaultProps: { size: "small" } },
  },
});
const query = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15000, retry: 1, refetchOnWindowFocus: false },
    mutations: { retry: false },
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={query}>
        <BrowserRouter>
          <AppProvider>
            <App />
          </AppProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
