"use client";
import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { pendingOverride, acceptOverride, rejectOverride } from "@/lib/apiOverride";

/**
 * Surfaces a `?api=` query override as an explicit choice (F1). Until the
 * user clicks Connect, no request uses the query base and no key is
 * attached — `apiBase()` is a pure getter and never writes on its own.
 */
export function BackendOverrideDialog() {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    setUrl(pendingOverride());
  }, []);

  if (!url) return null;

  return (
    <Dialog open onOpenChange={(o) => { if (!o) { rejectOverride(); setUrl(null); } }}>
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>Connect to a different backend?</DialogTitle>
          <DialogDescription>
            Connect this console to <span className="mono break-all">{url}</span>? Your sign-in key will be sent to this backend.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => { rejectOverride(); setUrl(null); }}
          >
            Cancel
          </Button>
          <Button onClick={() => acceptOverride(url)}>Connect</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
