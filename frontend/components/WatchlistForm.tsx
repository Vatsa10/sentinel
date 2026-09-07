"use client";
import { useState } from "react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export const PLATE_RE = /^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}\*?$|^[A-Z0-9]*\*$/;
/** Indian plate, wildcard `*` allowed for partial plates. */
export function validatePlate(v: string): string | null {
  const p = v.trim().toUpperCase();
  if (!p) return "Plate is required";
  if (p.includes("*")) return null; // wildcard partial plates allowed as-is
  if (!/^[A-Z]{2}[0-9]{1,2}[A-Z]{0,3}[0-9]{4}$/.test(p)) {
    return "Indian format, e.g. GJ01AB1234; partial plates allowed with *";
  }
  return null;
}

export interface WatchlistFormValues {
  plate: string;
  category: string;
  severity: string;
  owner_name: string;
  vehicle_make: string;
  vehicle_colour: string;
  vehicle_class: string;
  case_ref: string;
  source_db: string;
  notes: string;
}

const EMPTY: WatchlistFormValues = {
  plate: "", category: "suspect", severity: "medium", owner_name: "",
  vehicle_make: "", vehicle_colour: "", vehicle_class: "", case_ref: "",
  source_db: "MANUAL", notes: "",
};

export function WatchlistForm({
  onSubmit,
  submitting,
}: {
  onSubmit: (v: WatchlistFormValues) => void;
  submitting?: boolean;
}) {
  const [v, setV] = useState<WatchlistFormValues>(EMPTY);
  const [plateError, setPlateError] = useState<string | null>(null);

  const set = <K extends keyof WatchlistFormValues>(k: K, val: string) => setV((s) => ({ ...s, [k]: val }));

  return (
    <form
      id="watchlist-form"
      className="flex flex-col gap-3"
      onSubmit={(e) => {
        e.preventDefault();
        const err = validatePlate(v.plate);
        setPlateError(err);
        if (err) return;
        onSubmit({ ...v, plate: v.plate.trim().toUpperCase() });
      }}
    >
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="wl-plate">Plate</Label>
        <Input
          id="wl-plate"
          className="mono"
          value={v.plate}
          onChange={(e) => set("plate", e.target.value.toUpperCase())}
          onBlur={() => setPlateError(validatePlate(v.plate))}
          placeholder="GJ01AB1234"
          aria-invalid={!!plateError}
        />
        <p className="text-xs text-muted">
          Indian format, e.g. GJ01AB1234; partial plates allowed with *
        </p>
        {plateError && <p className="text-xs text-bad">{plateError}</p>}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-category">Category</Label>
          <Select value={v.category} onValueChange={(val) => set("category", String(val))}>
            <SelectTrigger id="wl-category" className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="suspect">Suspect</SelectItem>
              <SelectItem value="stolen">Stolen</SelectItem>
              <SelectItem value="wanted">Wanted</SelectItem>
              <SelectItem value="fir">FIR-linked</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-severity">Severity</Label>
          <Select value={v.severity} onValueChange={(val) => set("severity", String(val))}>
            <SelectTrigger id="wl-severity" className="w-full"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="low">Low</SelectItem>
              <SelectItem value="medium">Medium</SelectItem>
              <SelectItem value="high">High</SelectItem>
              <SelectItem value="critical">Critical</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-owner">Owner name</Label>
          <Input id="wl-owner" value={v.owner_name} onChange={(e) => set("owner_name", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-case">FIR / case ref</Label>
          <Input id="wl-case" value={v.case_ref} onChange={(e) => set("case_ref", e.target.value)} />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-make">Make</Label>
          <Input id="wl-make" value={v.vehicle_make} onChange={(e) => set("vehicle_make", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-colour">Colour</Label>
          <Input id="wl-colour" value={v.vehicle_colour} onChange={(e) => set("vehicle_colour", e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="wl-class">Class</Label>
          <Input id="wl-class" value={v.vehicle_class} onChange={(e) => set("vehicle_class", e.target.value)} />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="wl-source">Source database</Label>
        <Input id="wl-source" value={v.source_db} onChange={(e) => set("source_db", e.target.value)} />
      </div>

      <div className="flex flex-col gap-1.5">
        <Label htmlFor="wl-notes">Notes</Label>
        <Textarea id="wl-notes" value={v.notes} onChange={(e) => set("notes", e.target.value)} rows={3} />
      </div>

      <button type="submit" disabled={submitting} hidden aria-hidden />
    </form>
  );
}
