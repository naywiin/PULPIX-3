import { initializeApp, getApps, type FirebaseApp } from 'firebase/app';
import {
  createUserWithEmailAndPassword,
  getAuth,
  GoogleAuthProvider,
  onAuthStateChanged,
  sendPasswordResetEmail,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
  updateProfile,
  type Auth,
  type User,
} from 'firebase/auth';

export interface AuthUser {
  uid: string;
  email: string | null;
  displayName: string | null;
  photoURL: string | null;
}

const firebaseConfig = {
  apiKey: (import.meta.env.VITE_FIREBASE_API_KEY as string | undefined)?.trim() || '',
  authDomain: (import.meta.env.VITE_FIREBASE_AUTH_DOMAIN as string | undefined)?.trim() || '',
  projectId: (import.meta.env.VITE_FIREBASE_PROJECT_ID as string | undefined)?.trim() || '',
  appId: (import.meta.env.VITE_FIREBASE_APP_ID as string | undefined)?.trim() || '',
};

export const isFirebaseConfigured = Boolean(
  firebaseConfig.apiKey && firebaseConfig.authDomain && firebaseConfig.projectId,
);

let app: FirebaseApp | null = null;
let auth: Auth | null = null;

function ensureAuth(): Auth {
  if (!isFirebaseConfigured) {
    throw new Error('Firebase Auth no está configurado (faltan variables VITE_FIREBASE_*).');
  }
  if (!app) {
    app = getApps().length > 0 ? getApps()[0] : initializeApp(firebaseConfig);
  }
  if (!auth) {
    auth = getAuth(app);
  }
  return auth;
}

function toAuthUser(user: User | null): AuthUser | null {
  if (!user) return null;
  return {
    uid: user.uid,
    email: user.email,
    displayName: user.displayName,
    photoURL: user.photoURL,
  };
}

export function subscribeAuth(callback: (user: AuthUser | null) => void): () => void {
  if (!isFirebaseConfigured) {
    callback(null);
    return () => {};
  }
  return onAuthStateChanged(ensureAuth(), (u) => callback(toAuthUser(u)));
}

export async function getAuthToken(): Promise<string | null> {
  if (!isFirebaseConfigured) return null;
  const current = ensureAuth().currentUser;
  if (!current) return null;
  return current.getIdToken();
}

export async function signInWithGoogle(): Promise<AuthUser | null> {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: 'select_account' });
  const cred = await signInWithPopup(ensureAuth(), provider);
  return toAuthUser(cred.user);
}

export async function signInWithEmail(email: string, password: string): Promise<AuthUser | null> {
  const cred = await signInWithEmailAndPassword(ensureAuth(), email.trim(), password);
  return toAuthUser(cred.user);
}

export async function registerWithEmail(
  email: string,
  password: string,
  displayName?: string,
): Promise<AuthUser | null> {
  const cred = await createUserWithEmailAndPassword(ensureAuth(), email.trim(), password);
  if (displayName?.trim()) {
    await updateProfile(cred.user, { displayName: displayName.trim() });
  }
  return toAuthUser(cred.user);
}

export async function resetPassword(email: string): Promise<void> {
  await sendPasswordResetEmail(ensureAuth(), email.trim());
}

export async function signOutUser(): Promise<void> {
  if (!isFirebaseConfigured) return;
  await signOut(ensureAuth());
}

export function formatAuthError(error: unknown): string {
  const code = typeof error === 'object' && error !== null && 'code' in error ? String((error as { code: unknown }).code) : '';
  switch (code) {
    case 'auth/invalid-credential':
    case 'auth/wrong-password':
    case 'auth/user-not-found':
      return 'Correo o contraseña incorrectos.';
    case 'auth/email-already-in-use':
      return 'Este correo ya está registrado. Intenta iniciar sesión.';
    case 'auth/weak-password':
      return 'La contraseña debe tener al menos 6 caracteres.';
    case 'auth/invalid-email':
      return 'Ingresa un correo electrónico válido.';
    case 'auth/popup-closed-by-user':
      return 'Cerraste la ventana de Google antes de completar el acceso.';
    case 'auth/too-many-requests':
      return 'Demasiados intentos. Espera un momento e inténtalo de nuevo.';
    default:
      return error instanceof Error ? error.message : 'No se pudo completar el inicio de sesión.';
  }
}
