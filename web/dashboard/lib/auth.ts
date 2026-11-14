/** Shared auth contracts used by the server action and the login form. */

export type StaffUser = {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  mfa_enrolled: boolean;
};

export type LoginState = {
  status: "idle" | "mfa_required" | "error";
  message?: string;
};

export type LoginAction = (state: LoginState, formData: FormData) => Promise<LoginState>;
