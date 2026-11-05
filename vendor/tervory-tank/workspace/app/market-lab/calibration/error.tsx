"use client";
import { H7Shell } from "../h7-shell";
import { CalibrationView } from "../h7-view";
import "../h7.css";
export default function ErrorPage() { return <H7Shell><CalibrationView value={{ state: "error" }} /></H7Shell>; }
