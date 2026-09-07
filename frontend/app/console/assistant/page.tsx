"use client";
import { useRef, useState } from "react";
import Link from "next/link";
import { useMutation } from "@tanstack/react-query";
import { Send, ChevronDown } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { cn } from "cn";

interface AssistantResponse {
  answer: string;
  data: Record<string, unknown>;
  actions: { label: string; href?: string }[];
}

interface Turn {
  who: "user" | "netra";
  text: string;
  data?: Record<string, unknown>;
  actions?: { label: string; href?: string }[];
  intent?: string;
}

const CHIPS = [
  "Where was GJ01AB1234 last seen?",
  "How many trucks on cam13 in the last hour?",
  "Any cloned plates today?",
  "Which cameras are offline?",
];

function guessIntent(question: string, data: Record<string, unknown>): string {
  const q = question.toLowerCase();
  if (Object.keys(data).length === 0) return "unrecognised";
  if (/\b[a-z]{2}\s?\d{1,2}\s?[a-z]{0,3}\s?\d{3,4}\b/i.test(question)) return "plate trace";
  if (q.includes("clone")) return "cloned plates";
  if (q.includes("offline") || q.includes("camera") || q.includes("health")) return "camera health";
  if (q.includes("watchlist")) return "watchlist";
  if (q.includes("alert")) return "alerts";
  if (q.includes("unusual") || q.includes("baseline") || q.includes("anomal")) return "anomaly";
  return "detection summary";
}

export default function AssistantPage() {
  const [turns, setTurns] = useState<Turn[]>([
    { who: "netra", text: "Ask me about camera health, detections, alerts, the watchlist, coverage, cloned plates, or a specific registration number." },
  ]);
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const ask = useMutation({
    mutationFn: (question: string) => api<AssistantResponse>("/api/assistant", { method: "POST", json: { question } }),
    onSuccess: (r, question) => {
      setTurns((t) => [...t, { who: "netra", text: r.answer, data: r.data, actions: r.actions, intent: guessIntent(question, r.data) }]);
      queueMicrotask(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }));
    },
    onError: (e: unknown) => {
      setTurns((t) => [...t, { who: "netra", text: e instanceof ApiError ? `Error: ${e.message}` : "The assistant is unreachable." }]);
    },
  });

  function send(question: string) {
    const q = question.trim();
    if (!q) return;
    setTurns((t) => [...t, { who: "user", text: q }]);
    setInput("");
    ask.mutate(q);
    queueMicrotask(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" }));
  }

  return (
    <div className="flex h-[calc(100vh-7rem)] flex-col gap-3">
      <h1 className="text-lg font-semibold text-text">Assistant</h1>

      <div className="flex flex-wrap gap-2">
        {CHIPS.map((c) => (
          <button
            key={c}
            onClick={() => send(c)}
            className="rounded-full border border-border bg-surface px-3 py-1 text-xs text-muted hover:border-accent hover:text-text"
          >
            {c}
          </button>
        ))}
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto rounded-card border border-border bg-surface p-4">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {turns.map((t, i) => (
            <div key={i} className={cn("flex gap-2", t.who === "user" ? "flex-row-reverse" : "flex-row")}>
              {t.who === "netra" && (
                <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-bold text-accent" lang="hi">
                  नेत्र
                </span>
              )}
              <div className={cn("flex max-w-[80%] flex-col gap-1", t.who === "user" ? "items-end" : "items-start")}>
                <div
                  className={cn(
                    "rounded-card px-3 py-2 text-sm",
                    t.who === "user" ? "bg-accent/15 text-text" : "border border-border bg-surface-2 text-text"
                  )}
                >
                  {t.text}
                </div>
                {t.intent && <Badge variant="outline" className="text-[10px]">intent: {t.intent}</Badge>}
                {t.data && Object.keys(t.data).length > 0 && <SourcesDisclosure data={t.data} actions={t.actions ?? []} />}
              </div>
            </div>
          ))}
          {ask.isPending && (
            <div className="flex gap-2">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-bold text-accent" lang="hi">
                नेत्र
              </span>
              <div className="rounded-card border border-border bg-surface-2 px-3 py-2 text-sm text-muted">
                <span className="animate-pulse">···</span>
              </div>
            </div>
          )}
        </div>
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); send(input); }}
        className="flex items-end gap-2 rounded-card border border-border bg-surface p-2"
      >
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); }
          }}
          placeholder="Ask a question… (Enter to send, Shift+Enter for a new line)"
          className="min-h-9 flex-1 resize-none border-0 bg-transparent focus-visible:ring-0"
          rows={1}
        />
        <Button type="submit" disabled={!input.trim() || ask.isPending}>
          <Send className="size-4" />
        </Button>
      </form>
    </div>
  );
}

function SourcesDisclosure({ data, actions }: { data: Record<string, unknown>; actions: { label: string; href?: string }[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="w-full text-xs">
      <button onClick={() => setOpen((o) => !o)} className="flex items-center gap-1 text-muted hover:text-text">
        <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
        Sources
      </button>
      {open && (
        <div className="mt-1 flex flex-col gap-2 rounded-ctl border border-border bg-surface-2 p-2">
          <pre className="mono max-h-48 overflow-auto whitespace-pre-wrap break-all text-[11px] text-muted">
            {JSON.stringify(data, null, 2)}
          </pre>
          {actions.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {actions.map((a, i) => a.href ? (
                <Link key={i} href={a.href} className="text-accent hover:underline">{a.label}</Link>
              ) : (
                <span key={i} className="text-muted">{a.label}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
