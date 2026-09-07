"use client";
import { useState } from "react";
import { WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiBase, setApiBase } from "@/lib/api";
import { Brand } from "@/components/Brand";

export function Unreachable() {
  const [value, setValue] = useState(apiBase());

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4">
      <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-card border border-border bg-surface p-8 text-center">
        <Brand size="lg" />
        <WifiOff className="size-8 text-bad" />
        <div>
          <h1 className="text-lg font-semibold text-text">Backend unreachable</h1>
          <p className="mono mt-1 text-xs text-muted">{apiBase()}</p>
        </div>
        <p className="text-sm text-muted">
          NETRA cannot reach the API. Check the backend is running, or point
          this browser at a different address.
        </p>
        <form
          className="flex w-full flex-col gap-2 text-left"
          onSubmit={(e) => {
            e.preventDefault();
            setApiBase(value);
            location.reload();
          }}
        >
          <Label htmlFor="api-base">API base URL</Label>
          <Input
            id="api-base"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="http://localhost:8080"
          />
          <Button type="submit">Save and reload</Button>
        </form>
        <Button variant="outline" onClick={() => location.reload()}>
          Retry
        </Button>
      </div>
    </div>
  );
}
