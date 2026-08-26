"use client";

import { useEffect, useState } from "react";
import { listModels, ModelsResponse } from "@/lib/api";

interface Props {
  selected: string | null;
  onChange: (model: string) => void;
}

export default function ModelSelector({ selected, onChange }: Props) {
  const [models, setModels] = useState<ModelsResponse | null>(null);

  // Fetches the REAL currently-registered models from the backend (config.py's
  // MODEL_REGISTRY) rather than hardcoding a guess in the frontend — if a model gets added
  // or removed on the backend, this dropdown updates automatically with no frontend change.
  useEffect(() => {
    listModels()
      .then(setModels)
      .catch(() => setModels(null));
  }, []);

  if (!models) return null;

  return (
    <select
      value={selected || models.default}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700"
    >
      {Object.entries(models.available).map(([name, info]) => (
        <option key={name} value={name} title={info.description}>
          {name}
        </option>
      ))}
    </select>
  );
}
