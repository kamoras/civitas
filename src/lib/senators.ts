import { Senator } from "@/types/senator";
import senatorsData from "@/data/senators.json";

const senators = senatorsData as Senator[];

export function getAllSenators(): Senator[] {
  return senators;
}

export function getSenatorsByState(stateCode: string): Senator[] {
  return senators.filter((s) => s.state === stateCode);
}

export function getSenator(id: string): Senator | undefined {
  return senators.find((s) => s.id === id);
}
