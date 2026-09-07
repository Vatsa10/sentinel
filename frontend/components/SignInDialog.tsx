"use client";
import { useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

/**
 * `children`, when given, is rendered as the Dialog's own trigger button.
 * Open state always lives in AuthProvider (`authDialogOpen`) rather than
 * local state, so a 401/403 from anywhere in the app can reopen this dialog
 * even when no trigger for it happens to be on screen (spec §4.6).
 */
export function SignInDialog({ children }: { children?: React.ReactElement }) {
  const { signIn, authDialogOpen: open, setAuthDialogOpen: setOpen } = useAuth();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const role = await signIn(key);
      setOpen(false);
      setKey("");
      toast.success(`Signed in as ${role}`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setError("Key not recognised");
      } else {
        setError("Sign in failed");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) {
          setError(null);
          setKey("");
        }
      }}
    >
      {children && <DialogTrigger render={children} />}
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Sign in</DialogTitle>
          <DialogDescription>
            Keys are held only in this browser.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <Label htmlFor="api-key">API key</Label>
          <Input
            id="api-key"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoFocus
          />
          {error && <p className="text-xs text-bad">{error}</p>}
        </form>
        <DialogFooter>
          <Button onClick={submit} disabled={busy || !key.trim()}>
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
