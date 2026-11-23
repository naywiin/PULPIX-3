import { useState, type FormEvent } from 'react';
import {
  formatAuthError,
  isFirebaseConfigured,
  registerWithEmail,
  resetPassword,
  signInWithEmail,
  signInWithGoogle,
} from '../auth/firebase';

type Mode = 'login' | 'register' | 'reset';

export function AuthScreen() {
  const [mode, setMode] = useState<Mode>('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      if (mode === 'login') {
        await signInWithEmail(email, password);
      } else if (mode === 'register') {
        await registerWithEmail(email, password, name);
      } else {
        await resetPassword(email);
        setNotice('Te enviamos un enlace para restablecer tu contraseña. Revisa tu correo.');
      }
    } catch (err) {
      setError(formatAuthError(err));
    } finally {
      setBusy(false);
    }
  };

  const handleGoogle = async () => {
    setError(null);
    setNotice(null);
    setBusy(true);
    try {
      await signInWithGoogle();
    } catch (err) {
      setError(formatAuthError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '24px',
        background: 'var(--bg, #f8fafc)',
      }}
    >
      <div className="card" style={{ width: '100%', maxWidth: 420, padding: 28 }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ margin: 0, fontSize: '1.5rem' }}>Pulpix</h1>
          <p className="muted small" style={{ margin: '6px 0 0' }}>
            {mode === 'login' && 'Inicia sesión para ver y gestionar tus clientes de Meta Ads y Google Ads.'}
            {mode === 'register' && 'Crea tu cuenta gratis para conectar tus propias cuentas publicitarias.'}
            {mode === 'reset' && 'Ingresa tu correo para restablecer tu contraseña.'}
          </p>
        </div>

        {!isFirebaseConfigured && (
          <div className="mock-banner" style={{ marginBottom: 16 }}>
            Falta configurar las variables <code>VITE_FIREBASE_*</code> en el frontend para habilitar el inicio de sesión.
          </div>
        )}

        {mode !== 'reset' && (
          <>
            <button
              type="button"
              className="btn"
              onClick={handleGoogle}
              disabled={busy || !isFirebaseConfigured}
              style={{ width: '100%', justifyContent: 'center', marginBottom: 16 }}
            >
              Continuar con Google
            </button>
            <div className="muted small" style={{ textAlign: 'center', marginBottom: 16 }}>
              o con tu correo electrónico
            </div>
          </>
        )}

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {mode === 'register' && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span className="small muted">Nombre</span>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Tu nombre o agencia"
                autoComplete="name"
                disabled={busy}
                style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border, #cbd5e1)' }}
              />
            </label>
          )}

          <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span className="small muted">Correo electrónico</span>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="tu@correo.com"
              autoComplete="email"
              disabled={busy}
              style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border, #cbd5e1)' }}
            />
          </label>

          {mode !== 'reset' && (
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span className="small muted">Contraseña</span>
              <input
                type="password"
                required
                minLength={6}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Mínimo 6 caracteres"
                autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                disabled={busy}
                style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border, #cbd5e1)' }}
              />
            </label>
          )}

          {error && (
            <div role="alert" style={{ color: '#b91c1c', fontSize: '0.875rem' }}>
              {error}
            </div>
          )}
          {notice && (
            <div role="status" style={{ color: '#0f766e', fontSize: '0.875rem' }}>
              {notice}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            disabled={busy || !isFirebaseConfigured}
            style={{ width: '100%', justifyContent: 'center', marginTop: 4 }}
          >
            {busy
              ? 'Procesando…'
              : mode === 'login'
                ? 'Iniciar sesión'
                : mode === 'register'
                  ? 'Crear cuenta'
                  : 'Enviar enlace'}
          </button>
        </form>

        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 16, fontSize: '0.875rem' }}>
          {mode === 'login' ? (
            <>
              <button type="button" className="btn-link" onClick={() => { setMode('register'); setError(null); setNotice(null); }}>
                Crear cuenta nueva
              </button>
              <button type="button" className="btn-link" onClick={() => { setMode('reset'); setError(null); setNotice(null); }}>
                ¿Olvidaste tu contraseña?
              </button>
            </>
          ) : (
            <button type="button" className="btn-link" onClick={() => { setMode('login'); setError(null); setNotice(null); }}>
              Volver a iniciar sesión
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
