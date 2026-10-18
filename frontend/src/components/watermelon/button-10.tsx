import Link from "next/link";
import type { ComponentProps } from "react";
import styles from "./button-10.module.css";

type Props = ComponentProps<"button"> | ComponentProps<typeof Link>;

/** Watermelon button-10: sliding highlight, adapted to the requested violet metal. */
export default function Button10({ className, ...props }: Props) {
  const classes = [styles.button, className].filter(Boolean).join(" ");
  if ("href" in props) return <Link {...props} className={classes} />;
  return <button type="button" {...props} className={classes} />;
}
