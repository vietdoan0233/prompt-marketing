"use client";

import { useEffect, useState } from "react";

import { getActor, setActor } from "@/lib/client";

export function ActorPicker() {
  const [actor, setLocal] = useState("");
  useEffect(() => setLocal(getActor()), []);
  return (
    <label className="actor" title="Recorded on every audit event (prototype: no SSO)">
      acting as
      <input
        value={actor}
        onChange={(e) => {
          setLocal(e.target.value);
          setActor(e.target.value);
        }}
        aria-label="Reviewer identity"
      />
    </label>
  );
}
