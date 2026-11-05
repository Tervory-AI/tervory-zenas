"use client";
import { H7Shell } from "../../h7-shell";
import { BriefView } from "../../h7-view";
import "../../h7.css";
export default function ErrorPage() { return <H7Shell><BriefView value={{ state: "error" }} /></H7Shell>; }
