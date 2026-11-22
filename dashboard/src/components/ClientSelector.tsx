import type { ClientConfig } from '../config/clients';

export function ClientSelector({ clients, value, onChange }: { clients: ClientConfig[]; value: string; onChange: (id: string) => void }) {
  return (
    <label className="client-select">
      <span className="muted small">Cliente</span>
      <select className="input" value={value} onChange={(e) => onChange(e.target.value)} disabled={clients.length === 0}>
        {clients.length === 0 && <option value="">Sin clientes activos</option>}
        {clients.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
    </label>
  );
}
