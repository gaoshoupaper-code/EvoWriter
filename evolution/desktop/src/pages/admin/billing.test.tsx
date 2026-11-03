import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import AdminUsers from "./users";
import AdminInviteCodes from "./invite-codes";
import { adjustCredits, createInviteCodes, fetchInviteCodes, fetchUsers } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  fetchUsers: vi.fn(), updateUser: vi.fn(), resetPassword: vi.fn(), adjustCredits: vi.fn(),
  fetchInviteCodes: vi.fn(), createInviteCodes: vi.fn(), revokeInviteCode: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

afterEach(cleanup);
beforeEach(() => {
  vi.mocked(fetchUsers).mockResolvedValue([{
    user_id: "owner", username: "作者", credits_balance: 10.25, is_admin: false,
    is_super_admin: false, disabled: false, has_api_key: false, workspace_count: 1,
    created_at: "2026-11-03T00:00:00Z",
  }]);
  vi.mocked(fetchInviteCodes).mockResolvedValue([]);
  vi.mocked(adjustCredits).mockReset();
  vi.mocked(createInviteCodes).mockReset();
});

describe("积分运营", () => {
  it("调整金额保留小数，失败重试复用标识，修改内容产生新标识", async () => {
    vi.mocked(adjustCredits).mockRejectedValue(new Error("暂不可用"));
    render(<MemoryRouter><AdminUsers /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "调积分" }));
    const amount = screen.getByLabelText("调整额度（正=发放，负=扣减）");
    fireEvent.change(amount, { target: { value: "1.23" } });
    fireEvent.change(screen.getByLabelText("调整原因（必填）"), { target: { value: "测试发分" } });
    fireEvent.click(screen.getByRole("button", { name: "确认调整" }));
    await waitFor(() => expect(adjustCredits).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "确认调整" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "确认调整" }));
    await waitFor(() => expect(adjustCredits).toHaveBeenCalledTimes(2));
    const [first, retry] = vi.mocked(adjustCredits).mock.calls;
    expect(first.slice(0, 3)).toEqual(["owner", 1.23, "测试发分"]);
    expect(first[3]).toEqual(retry[3]);
    await waitFor(() => expect(screen.getByRole("button", { name: "确认调整" })).toBeEnabled());
    fireEvent.change(amount, { target: { value: "2.34" } });
    fireEvent.click(screen.getByRole("button", { name: "确认调整" }));
    await waitFor(() => expect(adjustCredits).toHaveBeenCalledTimes(3));
    expect(vi.mocked(adjustCredits).mock.calls[2][3]).not.toEqual(first[3]);
  });

  it("拒绝空原因和超过两位小数的调整，不截断金额", async () => {
    render(<MemoryRouter><AdminUsers /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "调积分" }));
    const amount = screen.getByLabelText("调整额度（正=发放，负=扣减）");
    fireEvent.change(amount, { target: { value: "1.23" } });
    fireEvent.click(screen.getByRole("button", { name: "确认调整" }));
    expect(adjustCredits).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("调整原因（必填）"), { target: { value: "纠错" } });
    fireEvent.change(amount, { target: { value: "1.234" } });
    fireEvent.click(screen.getByRole("button", { name: "确认调整" }));
    expect(adjustCredits).not.toHaveBeenCalled();
  });

  it("邀请码支持两位小数，发放必须有原因且失败重试不会重复发码", async () => {
    vi.mocked(createInviteCodes).mockRejectedValue(new Error("响应丢失"));
    render(<AdminInviteCodes />);
    fireEvent.click(await screen.findByRole("button", { name: "创建邀请码" }));
    fireEvent.change(screen.getByLabelText("每个邀请码的积分额度"), { target: { value: "12.34" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    expect(createInviteCodes).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("发放原因（必填）"), { target: { value: "试用名额" } });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(createInviteCodes).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: "创建" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => expect(createInviteCodes).toHaveBeenCalledTimes(2));
    const [first, retry] = vi.mocked(createInviteCodes).mock.calls;
    expect(first.slice(0, 3)).toEqual([1, 12.34, "试用名额"]);
    expect(first[3]).toEqual(retry[3]);
  });

  it("用户行提供直接查询本人账目的入口", async () => {
    render(<MemoryRouter><AdminUsers /></MemoryRouter>);
    expect(await screen.findByRole("link", { name: "查看账单" })).toHaveAttribute("href", "/admin/credits?user_id=owner");
  });
});
