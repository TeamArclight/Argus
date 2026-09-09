/**
 * Strongly-typed domain models re-exported from OpenAPI schema.
 * Strict TypeScript: zero 'any'.
 */
import type { components } from '@/generated/schema';

export type Schemas = components['schemas'];

// Core Authentication & Principal
export type AuthenticatedPrincipal = Schemas['AuthenticatedPrincipal'];
export type UserRole = Schemas['UserRole'];

// Tenders & Requirements
export type TenderRead = Schemas['TenderRead'];
export type TenderCreate = Schemas['TenderCreate'];
export type TenderRequirementRead = Schemas['TenderRequirementRead'];
export type TenderRequirementCreate = Schemas['TenderRequirementCreate'];
export type RequirementType = Schemas['RequirementType'];
export type OperatorEnum = Schemas['OperatorEnum'];

// Bidders & Documents
export type BidderRead = Schemas['BidderRead'];
export type BidderCreate = Schemas['BidderCreate'];
export type DocumentRead = Schemas['DocumentRead'];
export type DocumentType = Schemas['DocumentType'];
export type FactRead = Schemas['FactRead'];

// Verification & Processing Jobs
export type JobRead = Schemas['JobRead'];
export type JobEventRead = Schemas['JobEventRead'];
export type JobStage = Schemas['JobStage'];
export type JobStatus = Schemas['JobStatus'];
export type VerificationResultRead = Schemas['VerificationResultRead'];
export type VerificationStatus = Schemas['VerificationStatus'];
export type VerificationSource = Schemas['VerificationSource'];
export type VerificationMode = Schemas['VerificationMode'];

// Compliance Evaluation & Matrix
export type ComplianceStatus = Schemas['ComplianceStatus'];
export type ComplianceOverviewRead = Schemas['ComplianceOverviewRead'];
export type ComplianceMatrixRead = Schemas['ComplianceMatrixRead'];
export type ComplianceMatrixRow = Schemas['ComplianceMatrixRow'];
export type RuleEvaluationRead = Schemas['RuleEvaluationRead'];
export type ComplianceRunRead = Schemas['ComplianceRunRead'];
export type ComplianceRunSummaryRead = Schemas['ComplianceRunSummaryRead'];
export type ComplianceRunDetailRead = Schemas['ComplianceRunDetailRead'];

// Evidence & Risk
export type EvidenceRead = Schemas['EvidenceRead'];
export type RiskSignalRead = Schemas['RiskSignalRead'];
export type RiskSeverity = Schemas['RiskSeverity'];
export type RiskSummaryRead = Schemas['RiskSummaryRead'];

// Human Decisions & Reports
export type HumanDecisionCreate = Schemas['HumanDecisionCreate'];
export type HumanDecisionRead = Schemas['HumanDecisionRead'];
export type HumanDecisionStatus = Schemas['HumanDecisionStatus'];
export type ReportRead = Schemas['ReportRead'];

// System Health & Providers
export type ProviderHealthRead = Schemas['ProviderHealthRead'];
export type IntegrationsHealthResponse = Schemas['IntegrationsHealthResponse'];
export type IntegrationServiceStatus = Schemas['IntegrationServiceStatus'];
export type RAGQueryRequest = Schemas['RAGQueryRequest'];
export type RAGQueryResponse = Schemas['RAGQueryResponse'];
