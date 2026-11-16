import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, test } from "vitest";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Money } from "@/components/ui/money";

test("a negative amount carries a sign and the money-negative token, never colour alone", () => {
  render(<Money amount="-1234.50" />);
  const el = screen.getByText("-1,234.50");
  expect(el).toHaveClass("text-money-negative");
  expect(el).toHaveAttribute("data-negative", "true");
});

test("a liability balance sent unsigned is shown with a minus sign and the negative token", () => {
  render(<Money amount="250000.00" liability />);
  const el = screen.getByText("-250,000.00");
  expect(el).toHaveClass("text-money-negative");
});

test("a positive asset, zero and unknown amounts are not styled negative", () => {
  render(
    <>
      <Money amount="10.00" />
      <Money amount="0.00" liability />
      <Money amount={null} />
    </>,
  );
  expect(screen.getByText("10.00")).not.toHaveClass("text-money-negative");
  expect(screen.getByText("0.00")).not.toHaveClass("text-money-negative");
  expect(screen.getByText("0.00").textContent).not.toContain("-");
  expect(screen.getByText("—")).not.toHaveClass("text-money-negative");
});

test("a foreign amount keeps its currency attached", () => {
  render(<Money amount="-5.00" currency="GBP" />);
  expect(screen.getByText("GBP -5.00")).toHaveClass("text-money-negative");
});

test("button and badge variants map to semantic tokens", () => {
  render(
    <>
      <Button variant="destructive">Delete</Button>
      <Badge variant="flagged">Flagged</Badge>
      <Badge variant="superseded">Old</Badge>
    </>,
  );
  expect(screen.getByRole("button", { name: "Delete" })).toHaveClass("bg-destructive");
  expect(screen.getByText("Flagged")).toHaveClass("text-warning");
  expect(screen.getByText("Old")).toHaveClass("text-muted-foreground");
});

function Confirm({ onConfirm }: { onConfirm: () => void }) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button variant="destructive">Close account</Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Close the Everyday account?</AlertDialogTitle>
          <AlertDialogDescription>It stops counting in the Position.</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>Close account</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

test("an AlertDialog names the thing, Cancel does not act, and focus returns to the trigger", async () => {
  let confirmed = 0;
  render(<Confirm onConfirm={() => confirmed++} />);
  const trigger = screen.getByRole("button", { name: "Close account" });
  trigger.focus();
  fireEvent.click(trigger);
  const dialog = await screen.findByRole("alertdialog", { name: "Close the Everyday account?" });
  expect(dialog).toHaveTextContent("It stops counting in the Position.");

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(confirmed).toBe(0);
  await waitFor(() => expect(trigger).toHaveFocus());

  fireEvent.click(trigger);
  await screen.findByRole("alertdialog");
  fireEvent.click(screen.getAllByRole("button", { name: "Close account" }).at(-1)!);
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(confirmed).toBe(1);
  await waitFor(() => expect(trigger).toHaveFocus());
});
