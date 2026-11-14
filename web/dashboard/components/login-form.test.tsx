import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/login-form";
import type { LoginState } from "@/lib/auth";

const MFA_MESSAGE = "Enter the 6-digit code from your authenticator app.";

describe("LoginForm", () => {
  it("does not ask for a code until the API requests one", () => {
    render(<LoginForm action={vi.fn()} />);

    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
    expect(screen.queryByLabelText("Authentication code")).not.toBeInTheDocument();
  });

  it("reveals the code step, keeps typed credentials and submits the code", async () => {
    const user = userEvent.setup();
    const action = vi.fn(
      async (_previous: LoginState, formData: FormData): Promise<LoginState> => {
        if (!formData.get("totp_code")) {
          return { status: "mfa_required", message: MFA_MESSAGE };
        }
        return { status: "error", message: "That code is not valid. Try again." };
      },
    );

    render(<LoginForm action={action} />);

    const email = screen.getByLabelText("Email");
    const password = screen.getByLabelText("Password");
    await user.type(email, "owner@example.test");
    await user.type(password, "synthetic-pass");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const code = await screen.findByLabelText("Authentication code");
    expect(screen.getByRole("status")).toHaveTextContent(MFA_MESSAGE);
    expect(email).toHaveValue("owner@example.test");
    expect(password).toHaveValue("synthetic-pass");

    await user.type(code, "123456");
    await user.click(screen.getByRole("button", { name: "Verify code" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("That code is not valid.");
    expect(action).toHaveBeenCalledTimes(2);
    const submitted = action.mock.calls[1][1] as FormData;
    expect(submitted.get("email")).toBe("owner@example.test");
    expect(submitted.get("password")).toBe("synthetic-pass");
    expect(submitted.get("totp_code")).toBe("123456");
  });

  it("announces a failure as an alert and links the fields to it", async () => {
    const user = userEvent.setup();
    const action = vi.fn(
      async (): Promise<LoginState> => ({
        status: "error",
        message: "Email or password is incorrect.",
      }),
    );

    render(<LoginForm action={action} />);

    await user.type(screen.getByLabelText("Email"), "staff@example.test");
    await user.type(screen.getByLabelText("Password"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Email or password is incorrect.");

    const email = screen.getByLabelText("Email");
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAttribute("aria-describedby", "login-error");
  });
});
