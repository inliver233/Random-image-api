const STORAGE_KEY = "admin_token";

export function getAdminToken(): string | null {
  const value = localStorage.getItem(STORAGE_KEY);
  if (!value) return null;
  return value;
}

export function setAdminToken(token: string): void {
  localStorage.setItem(STORAGE_KEY, token);
}

export function clearAdminToken(): void {
  localStorage.removeItem(STORAGE_KEY);
}

