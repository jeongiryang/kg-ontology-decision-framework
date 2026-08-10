// Generated from ontology/ontology_spec.json V0.2.
// Re-runnable: every statement uses IF NOT EXISTS.

CREATE CONSTRAINT institution_institution_id_unique IF NOT EXISTS
FOR (n:Institution)
REQUIRE n.institution_id IS UNIQUE;

CREATE CONSTRAINT department_department_id_unique IF NOT EXISTS
FOR (n:Department)
REQUIRE n.department_id IS UNIQUE;

CREATE CONSTRAINT document_document_id_unique IF NOT EXISTS
FOR (n:Document)
REQUIRE n.document_id IS UNIQUE;

CREATE CONSTRAINT curriculum_version_curriculum_id_unique IF NOT EXISTS
FOR (n:CurriculumVersion)
REQUIRE n.curriculum_id IS UNIQUE;

CREATE CONSTRAINT course_course_id_unique IF NOT EXISTS
FOR (n:Course)
REQUIRE n.course_id IS UNIQUE;

CREATE CONSTRAINT course_offering_offering_id_unique IF NOT EXISTS
FOR (n:CourseOffering)
REQUIRE n.offering_id IS UNIQUE;

CREATE CONSTRAINT education_area_area_id_unique IF NOT EXISTS
FOR (n:EducationArea)
REQUIRE n.area_id IS UNIQUE;

CREATE CONSTRAINT rule_rule_id_unique IF NOT EXISTS
FOR (n:Rule)
REQUIRE n.rule_id IS UNIQUE;

CREATE CONSTRAINT applicability_scope_scope_id_unique IF NOT EXISTS
FOR (n:ApplicabilityScope)
REQUIRE n.scope_id IS UNIQUE;

CREATE CONSTRAINT evidence_evidence_id_unique IF NOT EXISTS
FOR (n:Evidence)
REQUIRE n.evidence_id IS UNIQUE;

CREATE CONSTRAINT condition_group_condition_group_id_unique IF NOT EXISTS
FOR (n:ConditionGroup)
REQUIRE n.condition_group_id IS UNIQUE;

CREATE CONSTRAINT condition_condition_id_unique IF NOT EXISTS
FOR (n:Condition)
REQUIRE n.condition_id IS UNIQUE;

CREATE CONSTRAINT talent_profile_talent_profile_id_unique IF NOT EXISTS
FOR (n:TalentProfile)
REQUIRE n.talent_profile_id IS UNIQUE;

CREATE CONSTRAINT education_goal_goal_id_unique IF NOT EXISTS
FOR (n:EducationGoal)
REQUIRE n.goal_id IS UNIQUE;

CREATE CONSTRAINT career_field_career_field_id_unique IF NOT EXISTS
FOR (n:CareerField)
REQUIRE n.career_field_id IS UNIQUE;

CREATE CONSTRAINT competency_competency_id_unique IF NOT EXISTS
FOR (n:Competency)
REQUIRE n.competency_id IS UNIQUE;

CREATE CONSTRAINT alignment_alignment_id_unique IF NOT EXISTS
FOR (n:Alignment)
REQUIRE n.alignment_id IS UNIQUE;

CREATE CONSTRAINT roadmap_entry_roadmap_entry_id_unique IF NOT EXISTS
FOR (n:RoadmapEntry)
REQUIRE n.roadmap_entry_id IS UNIQUE;

CREATE CONSTRAINT course_recommendation_recommendation_id_unique IF NOT EXISTS
FOR (n:CourseRecommendation)
REQUIRE n.recommendation_id IS UNIQUE;

CREATE CONSTRAINT curriculum_aggregate_aggregate_id_unique IF NOT EXISTS
FOR (n:CurriculumAggregate)
REQUIRE n.aggregate_id IS UNIQUE;

CREATE CONSTRAINT credit_allocation_allocation_id_unique IF NOT EXISTS
FOR (n:CreditAllocation)
REQUIRE n.allocation_id IS UNIQUE;

CREATE CONSTRAINT correction_record_correction_id_unique IF NOT EXISTS
FOR (n:CorrectionRecord)
REQUIRE n.correction_id IS UNIQUE;

CREATE INDEX course_course_code_idx IF NOT EXISTS
FOR (n:Course)
ON (n.course_code);

CREATE INDEX course_name_ko_idx IF NOT EXISTS
FOR (n:Course)
ON (n.name_ko);

CREATE INDEX curriculum_version_academic_year_idx IF NOT EXISTS
FOR (n:CurriculumVersion)
ON (n.academic_year);

CREATE INDEX rule_status_idx IF NOT EXISTS
FOR (n:Rule)
ON (n.status);

CREATE INDEX course_offering_status_idx IF NOT EXISTS
FOR (n:CourseOffering)
ON (n.status);

CREATE INDEX evidence_excerpt_page_idx IF NOT EXISTS
FOR (n:Evidence)
ON (n.excerpt_page);

CREATE INDEX evidence_source_pdf_page_idx IF NOT EXISTS
FOR (n:Evidence)
ON (n.source_pdf_page);
