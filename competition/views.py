from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication
from django.db import transaction
from datetime import timedelta
from django.utils.timezone import now
from django.shortcuts import get_object_or_404


from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from djangorestframework_camel_case.parser import CamelCaseFormParser, CamelCaseMultiPartParser
from rest_framework.parsers import MultiPartParser, FormParser


from .models import Competition, CompetitionResult
from users.models import CustomUser
from matchtype.models import MatchType
from applicant_info.models import ApplicantInfo
from applicant.models import Applicant
from participant.models import Participant
from participant_info.models import ParticipantInfo
from .serializers import CompetitionListSerializer, CompetitionDetailInfoSerializer, CompetitionApplySerializer, MyCompetitionSerializer, CompetitionResultSerializer
from applicant_info.serializers import ApplicantInfoSerializer, CompetitionApplicantInfoSerializer
from applicant.serializers import ApplicantSerializer, CompetitionApplicantSerializer
from users.serializers import UserWithClubInfoSerializer




## 대회 리스트
class CompetitionListView(APIView):
    """
    대회 리스트
    """
    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        operation_summary='전체 대회 리스트 조회',
        operation_description='쿼리파라미터를 사용하여 성별 / 단, 복식 조건 조회 가능 (예시 /api/v1/competitions?gender=male&matchType=single)',
        manual_parameters=[
            openapi.Parameter('gender', openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter('matchType', openapi.IN_QUERY, type=openapi.TYPE_STRING)
        ],
        responses={
            200: CompetitionListSerializer(many=True),
        }
    )
    
    def get(self, request):
        # 쿼리 파라미터로부터 gender와 type 가져옴
        gender = request.query_params.get('gender')
        type = request.query_params.get('match_type')

        if gender and type:
            competitions = Competition.objects.filter(match_type__gender=gender, match_type__type=type)
        else:
            competitions = Competition.objects.all()

        serializer = CompetitionListSerializer(competitions, many=True, context={'request': request})
        
        return Response(serializer.data)
    

## 대회 상세정보
class CompetitionDetailView(APIView):
    """
    대회 상세보기
    """
    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        operation_summary='대회 상세 보기',
        operation_description='하나의 대회에 대해 상세하게 보여준다.',
        responses={
            200: CompetitionDetailInfoSerializer(many=True),
            404: 'Not Found'
        }
    )
    
    def get(self, request, pk):
        try:
            competition = Competition.objects.get(pk=pk)
            serializer = CompetitionDetailInfoSerializer(competition, context={'request': request})
            return Response(serializer.data)
        except Competition.DoesNotExist:
            return Response({'error': '대회를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)



## 파트너 조회
class PartnerSearchView(APIView):
    """
    파트너 검색
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary='파트너 검색',
        operation_description='쿼리파라미터를 사용하여 파트너 검색 결과 목록을 불러온다 (예시 /api/v1/competitions/1/partnersearch/?query=검색어)',
        manual_parameters=[
            openapi.Parameter('query', openapi.IN_QUERY, description="ex) 유", type=openapi.TYPE_STRING),
        ],
        responses={
            200: UserWithClubInfoSerializer(many=True),
            401: 'Authentication Error',
            403: 'Permission Denied',
            404: 'Not Found'
        }
    )

    def get(self, request, pk):
        search_query = request.query_params.get('query', '')
        
        if not search_query:
            return Response({'error': '검색어를 입력해주세요.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # 검색어에 따라 사용자 필터링
        partners = CustomUser.objects.filter(username__icontains=search_query)
        
        competition = Competition.objects.get(pk=pk)

        partner_list = []

        for partner in partners:
            status_message = self.check_partner_eligibility(partner, competition)
            partner_info = UserWithClubInfoSerializer(partner).data
            partner_info['application_status'] = status_message
            partner_list.append(partner_info)

        return Response(partner_list, status=status.HTTP_200_OK)

    # 파트너의 신청 가능 상태 확인 함수
    def check_partner_eligibility(self, partner, competition):
        
        # 파트너 중복 신청 확인
        duplicates = Applicant.objects.filter(applicant_info__competition=competition, user=partner).order_by('-created_at').first()
        if duplicates:
            duplicates_status = duplicates.applicant_info.status
            if 'user_canceled' not in duplicates_status:
                if 'admin_canceled' in duplicates_status:
                    return False
                return False

        # 파트너 성별 확인
        if competition.match_type.gender != 'mix' and competition.match_type.gender != partner.gender:
            return False

        # 파트너 티어 확인
        if competition.tier not in partner.tiers.all():
            return False

        # 모든 조건을 통과하면 신청 가능
        return True




## 대회신청
class CompetitionApplyView(APIView):
    """
    대회 신청
    """

    permission_classes = [IsAuthenticated]
    parser_classes = (CamelCaseFormParser, CamelCaseMultiPartParser)
    
    @swagger_auto_schema(
        operation_summary='대회 신청',
        operation_description='대회 신청 API',
        manual_parameters=[
            openapi.Parameter('partner_id', openapi.IN_FORM, type=openapi.TYPE_INTEGER, description='Partner ID(복식일 경우에만)', required=False),
            openapi.Parameter('code', openapi.IN_FORM, type=openapi.TYPE_STRING, description='Competition Code', required=True),
        ],
        responses={
            201: 'Created',
            400: 'Invalid input',
            404: 'Not found',
        }
    )
    
    def post(self, request, pk):
        
        try: # 대회 조회
            competition = Competition.objects.get(pk=pk)
        except Competition.DoesNotExist:
            return Response({'error': '대회를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
        
        # 코드 확인
        submitted_code = request.data.get('code')
        if submitted_code != competition.code:
            return Response({'error': '제출된 코드가 유효하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)

        applicant = request.user 
        
        # 신청자 중복 신청 확인
        duplicates = Applicant.objects.filter(applicant_info__competition=competition, user=applicant).order_by('-created_at').first() # 기존 참가자 정보 유무 확인
        if duplicates is not None:
            duplicates_status = duplicates.applicant_info.status
        
            if duplicates_status:
                if 'user_canceled' not in duplicates_status : # 사용자취소 이외의 상태인 참가신청정보가 있을 때
                    if 'admin_canceled' in duplicates_status : # 관리자 취소처리된 참가신청정보가 있을 때
                        return Response({'error': '관리자 권한으로 참가신청이 취소되었습니다.'}, status=status.HTTP_400_BAD_REQUEST)
                    return Response({'error': '이미 신청된 대회입니다.'}, status=status.HTTP_400_BAD_REQUEST)

        # 진행 중인 대회 신청시 에러메세지
        if competition.status == 'during':
            return Response({'error': '진행중인 대회에는 신청할 수 없습니다.'})
        
        # 끝낸 대회 신청시 에러메세지
        if competition.status == 'ended':
            return Response({'error': '종료된 대회입니다.'})


        # 신청자 성별이 대회에 적합한지 확인
        if competition.match_type.gender != 'mix': # 대회가 혼성이나 팀 경기가 아니고
            if competition.match_type.gender != applicant.gender:
                return Response({'error': f'이 대회는 {competition.match_type.gender} 경기이므로 신청할 수 없습니다.'})
                        
        # 티어 구분
        if competition.tier not in applicant.tiers.all():
            return Response({'error': '실력 제한 규정으로 참가 신청을 할 수 없습니다.'})  




        ### 단식 신청
        if competition.match_type.type == 'single':
            return self.handle_singles(request, competition, applicant)
        
        
        
        ### 복식 신청
        if competition.match_type.type == 'double':
            # 파트너 생성
            partner_id = request.data.get('partner_id')  #신청 폼에서 제공된 파트너의 ID
            
            # 파트너 선택 확인
            if not partner_id:
                    return Response({'error': '파트너를 선택하세요.'}, status=status.HTTP_400_BAD_REQUEST)
            
            partner = CustomUser.objects.get(id=partner_id) # 파트너 인스턴스 생성
            
            # 혼성 확인
            if competition.match_type.gender == 'mix' and applicant.gender == partner.gender:
                return Response({'error': '혼성 경기는 서로 다른 성별의 파트너가 필요합니다.'}, status=status.HTTP_400_BAD_REQUEST)
            
            # 파트너 성별 확인
            elif partner.gender != competition.match_type.gender and competition.match_type.gender != 'mix':
                return Response({'error': '선택한 파트너의 성별이 경기의 조건과 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)
        
            # 자기 자신 선택 불가
            elif applicant.id == partner.id:
                return Response({'error': '본인을 파트너로 선택할 수 없습니다.'}, status=status.HTTP_400_BAD_REQUEST)
            
            # 파트너 티어 확인
            elif competition.tier not in partner.tiers.all():
                return Response({'error': '선택한 파트너 부가 경기의 조건과 일치하지 않습니다.'}, status=status.HTTP_400_BAD_REQUEST)
            
            
            # 파트너 중복 신청 확인
            elif partner_id:
                duplicates = Applicant.objects.filter(applicant_info__competition=competition, user_id=partner_id).order_by('-created_at').first() # 기존 참가자 정보 유무 확인

                if duplicates is not None:
                    duplicates_status = duplicates.applicant_info.status
        
                    if duplicates_status:
                        if 'user_canceled' not in duplicates_status :
                            if 'admin_canceled' in duplicates_status : 
                                return Response({'error': '관리자 권한으로 참가신청이 취소되었습니다.'}, status=status.HTTP_400_BAD_REQUEST)
                            return Response({'error': '선택하신 파트너는 이미 해당 대회를 신청하셨습니다.'}, status=status.HTTP_400_BAD_REQUEST)
            
            return self.handle_doubles(request, competition, applicant, partner)
        
        else:
            return Response({'error': '대회신청이 정상적으로 진행되지 않았습니다. 신청정보를 확인해주세요.'}, status=status.HTTP_400_BAD_REQUEST)
        
        
        
        
        
    
    ### 단식 신청 처리 로직
    def handle_singles(self, request, competition, applicant):
        
        # 대기 처리
        current_applicants_count = ApplicantInfo.objects.filter(competition=competition).count()
        max_participants = competition.max_participants
        waiting_number = None
        
        # 대기자 처리 
        if current_applicants_count >= max_participants:
            max_waiting_number = ApplicantInfo.objects.filter(competition=competition, waiting_number__isnull=False).count()
            waiting_number = max_waiting_number + 1
        
        # applicant_info 저장       
        applicant_info_data = {
                    'competition': competition.id,
                    'waiting_number': waiting_number,
                    'expired_date': now() + timedelta(days=competition.deposit_date)
            }
        serializer = ApplicantInfoSerializer(data=applicant_info_data)
        if serializer.is_valid():
            applicant_info = serializer.save() 
        else:
                return Response(ApplicantInfoSerializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        
        # applicant 저장
        expired_date = applicant_info_data['expired_date']
        applicant_data = {'user': applicant.id, 'applicant_info': applicant_info.id}
        applicant_serializer = ApplicantSerializer(data=applicant_data)
        if applicant_serializer.is_valid():
            applicant_serializer.save()  # 신청자 생성
            
            # 대회신청 / 대기신청 응답
            applicant_info_status = '대기신청 완료' if waiting_number else '신청 완료'
            competition_serializer = CompetitionApplySerializer(competition)
            response_data = {
                'status': f'{applicant_info_status}',
                'applicant_info': {
                    'applicant': applicant.username,
                    'phone': applicant.phone
                },
                'competition_info': competition_serializer.data,
                'expired_date': expired_date  
            }
            
            return Response(response_data, status=status.HTTP_201_CREATED)
        else:
            return Response(applicant_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    
    
    ### 복식 신청 처리 로직
    def handle_doubles(self, request, competition, applicant, partner):
        with transaction.atomic(): # 2개 신청 동시 처리
            
            current_applicants_count = ApplicantInfo.objects.filter(competition=competition).count()
            max_participants = competition.max_participants
            waiting_number = None
            
            # 대기자 처리
            if current_applicants_count >= max_participants:
                max_waiting_number = ApplicantInfo.objects.filter(competition=competition, waiting_number__isnull=False).count()
                waiting_number = max_waiting_number + 1
            
            # applicant_info 저장 
            applicant_info_data = {
                    'competition': competition.id,
                    'waiting_number': waiting_number,
                    'expired_date': now() + timedelta(days=competition.deposit_date)
            }
            serializer = ApplicantInfoSerializer(data=applicant_info_data)
            if serializer.is_valid():
                applicant_info = serializer.save()
            else:
                return Response(ApplicantInfoSerializer.errors, status=status.HTTP_400_BAD_REQUEST)
            
            expired_date = applicant_info_data['expired_date']
            saved_applicants = []
            
            for user in [applicant, partner]:
                applicant_data = {'user': user.id, 'applicant_info': applicant_info.id}
                applicant_serializer = ApplicantSerializer(data=applicant_data)
                if not applicant_serializer.is_valid():
                    return Response(applicant_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
                saved_applicant = applicant_serializer.save() # 신청자 생성
                saved_applicants.append(saved_applicant) # 신청자 리스트 생성(복식 경우 2명의 신청자 return)
            
            # 대회신청 / 대기신청 응답    
            applicant_info_status = '대기신청 완료' if waiting_number else '신청 완료'
            competition_serializer = CompetitionApplySerializer(competition)
            response_data = {
                'status': f'{applicant_info_status}',
                'applicant_info': {
                    'applicant': applicant.username,
                    'phone': applicant.phone
                },
                'competition_info': competition_serializer.data,
                'expired_date': expired_date  
            }

        return Response(response_data, status=status.HTTP_201_CREATED)
    

## 대회 신청 결과 조회
class CompetitionApplyResultView(APIView):
    """
    대회 신청 결과 조회
    """

    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
        operation_summary='대회 신청 결과 조회',
        operation_description='대회 신청 결과 조회. id값은 대회id 입니다.'
    )
    
    def get(self, request, pk):
        
        user = request.user
        
        # 대회를 찾을 수 없는 경우
        try :
            competition = Competition.objects.get(pk=pk)
        except Competition.DoesNotExist :
            return Response({'error':'존재하지 않는 대회입니다.'}, status=status.HTTP_404_NOT_FOUND)
        
        # 단식 대회의 경우
        if competition.match_type.type == 'single':
            # 신청정보가 입금 대기, 참가 대기중, 참가완료인 경우만
            try :
                applicant_1 = Applicant.objects.select_related('user').get(
                    applicant_info__competition=competition,
                    user=user,
                    applicant_info__status__in=['unpaid','pending_participation','confirmed_participation']
                )
            except Applicant.DoesNotExist :
                return Response({'error':'신청자 정보가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

            applicant1_serializer = CompetitionApplicantSerializer(applicant_1)
            
            applicants = [applicant1_serializer.data]

            
                
        # 복식 대회의 경우
        # applicant_info fk가 똑같은 유저
        else : 
            try :
                applicant_1 = Applicant.objects.select_related('user').get(
                    applicant_info__competition=competition,
                    user=user,
                    applicant_info__status__in=['unpaid','pending_participation','confirmed_participation']
                )
        
            except Applicant.DoesNotExist :
                return Response({'error':'신청자 정보가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)            
            find_info = applicant_1.applicant_info
            applicant_list = Applicant.objects.filter(applicant_info=find_info).select_related('user')

            if len(applicant_list) > 1:

                applicant_1,applicant_2 = applicant_list
                applicant1_serializer = CompetitionApplicantSerializer(applicant_1)
                applicant2_serializer = CompetitionApplicantSerializer(applicant_2)

                applicants = [applicant1_serializer.data, applicant2_serializer.data]
            else :
                return Response({'error':'신청자 정보가 없습니다.'}, status=status.HTTP_404_NOT_FOUND)

        # 대회 신청정보
        applicant_infos = applicant_1.applicant_info

        applicant_info_serializer = CompetitionApplicantInfoSerializer(applicant_infos)
        competition_serializer = CompetitionApplySerializer(competition)
        
        response_data = {'applicants':applicants, 
                        'applicantsInfo':applicant_info_serializer.data, 
                        'competitionInfo': competition_serializer.data}

        return Response(response_data, status=status.HTTP_200_OK)

## 신청 취소
class CompetitionCancelView(APIView):
    """
    대회 신청 취소
    """
    permission_classes = [IsAuthenticated]
    
    @swagger_auto_schema(
    operation_summary='대회 신청 취소',
    operation_description='대회 신청 취소 API. URL의 id는 ApplicantInfo의 ID를 의미합니다.',
    responses={
        200: 'OK: 사용자 취소 완료',
        401: 'Unautorized: 자격 인증이 되지 않았습니다.',
        403: 'FOBIDDEN: 이 작업을 수행할 권한이 없습니다.',
        404: 'Not Found: 신청정보를 찾지 못했습니다.',
        409: 'CONFLICT: 이미 취소 처리된 신청입니다.'
    }
    )

    def put(self, request, pk):
        # 신청 정보 변수 생성
        applicant_info = get_object_or_404(ApplicantInfo, pk=pk)

        # 현재 로그인한 사용자가 해당 ApplicantInfo에 속한 Applicant 중 하나인지 확인
        if not Applicant.objects.filter(applicant_info=applicant_info, user=request.user).exists():
            return Response({'error': '이 작업을 수행할 권한이 없습니다.'}, status=status.HTTP_403_FORBIDDEN)

        # 사용자가 취소할 수 있는 상태인지 확인
        if applicant_info.status in ['user_canceled', 'admin_cancelled']:
            return Response({'error': '이미 취소 처리된 신청입니다.'}, status=status.HTTP_409_CONFLICT)      
        
        participants = []
        # 참가 완료 상태인 경우 소프트 딜리트 수행
        if applicant_info.status == 'confirmed_participation':
            user_participant = Participant.objects.filter(participant_info__competition=applicant_info.competition, user=request.user, is_deleted=False).first()
            print(f"user_participant: {user_participant}")
            partner_participant = Participant.objects.filter(participant_info=user_participant.participant_info, is_deleted=False).exclude(user=request.user).first()
            print(f"partner_participant: {partner_participant}")
            if user_participant:
                participants.append(user_participant)
                if partner_participant:
                    participants.append(partner_participant)

        
            
            for participant in participants:
                # 참가정보 삭제(소프트 딜리트)
                participant.participant_info.is_deleted = True
                participant.participant_info.save()

                participant.is_deleted = True
                participant.save()
        
        # 신청 상태를 '사용자 취소'로 업데이트
        applicant_info.status = 'user_canceled'
        applicant_info.save()
        
        # 대기 중인 신청자 중 대기 순번이 빠른 신청자를 찾음
        waiting_applicant = ApplicantInfo.objects.filter(waiting_number__isnull=False).order_by('waiting_number').first()
        
        if waiting_applicant:
            # 대기자를 참가자로 변경
            if waiting_applicant.status == 'unpaid': # 대기자가 '입금 대기' 상태였을 경우 waiting_number만 None 처리
                waiting_applicant.waiting_number = None
            elif waiting_applicant.status == 'pending_participation': # 대기자가 '참가 대기중' 상태였을 경우 waiting_number을 None 처리, '참가 완료'로 상태 변경
                waiting_applicant.waiting_number = None
                waiting_applicant.status = 'confirmed_participation'
            waiting_applicant.save()

        # 업데이트된 정보를 반환
        response_data = {
            'status': '사용자 취소 완료',
            'competition': applicant_info.competition.name
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
    

## 참가 신청한 대회 조회
class MyCompetitionListView(APIView):
    """
    참가 신청한 대회 조회
    """

    permission_classes=[IsAuthenticated]
    
    @swagger_auto_schema(
        operation_summary='나의 대회 목록 조회',
        operation_description='로그인한 사용자의 대회 목록을 조회합니다.',
        manual_parameters=[
            openapi.Parameter('count', openapi.IN_QUERY, description='가져올 대회 수', type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter('status', openapi.IN_QUERY, description='대회 상태 (before, during, ended, noapply)', type=openapi.TYPE_STRING, required=False),
        ],
        responses={
        200: openapi.Response(
            description='OK')
        }
    )

    def get(self, request):

        user = request.user
        participant_competitions = Participant.objects.filter(user=user).values_list('participant_info__competition', flat=True)
        applicant_competitions = Applicant.objects.filter(user=user,applicant_info__status__in=['unpaid','pending_participation','confirmed_participation']).values_list('applicant_info__competition', flat=True)

        # 쿼리 파라미터로 count, status 받기
        competition_count = request.query_params.get('count', None)
        competition_status = request.query_params.get('status', None)

        # count type (str -> int)
        if competition_count is not None:
            competition_count = int(competition_count)


        # 참가 신청한 대회 - 진행전 - 대기포함
        # applicant_info status가 취소되지 않은 것들만
        before_list = Competition.objects.filter(
            status='before',
            id__in=applicant_competitions).order_by('start_date').distinct().prefetch_related('applicants__user')


        before_my_competitions = MyCompetitionSerializer(before_list, many=True, context={'request': request}).data



        # 참가 신청한 대회 - 진행중 - 대기 미포함
        during_list = Competition.objects.filter(
            status='during',
            id__in=participant_competitions
        ).order_by('-start_date').distinct().prefetch_related('applicants__user')

        during_my_competitions = MyCompetitionSerializer(during_list, many=True, context={'request': request}).data

        # 참가 신청한 대회 - 종료 - 대기 미포함
        ended_list = Competition.objects.filter(
            status='ended',
            id__in=participant_competitions
        ).order_by('-start_date').distinct().prefetch_related('applicants__user')

        ended_my_competitions = MyCompetitionSerializer(ended_list, many=True, context={'request': request}).data


        
        user_tiers = user.tiers.all()

        # 신청 가능한 대회 중 시작되지 않은 대회
        not_apply_list = Competition.objects.filter(
            status='before', tier__in=user_tiers).exclude(id__in=applicant_competitions).order_by('start_date').distinct().prefetch_related('applicants__user')


        not_apply_competitions = MyCompetitionSerializer(not_apply_list, many=True, context={'request': request}).data

        # 진행 전인 대회
        if competition_status == 'before':
            competition_list = before_my_competitions[:competition_count] if competition_count else before_my_competitions

            if not competition_list:
                return Response({'detail':'진행 전인 대회가 없습니다.'}, status=status.HTTP_200_OK)

        # 진행 중인 대회
        elif competition_status == 'during':
            competition_list = during_my_competitions[:competition_count] if competition_count else during_my_competitions

            if not competition_list:
                return Response({'detail':'진행 중인 대회가 없습니다.'},status=status.HTTP_200_OK)

        # 종료한 대회
        elif competition_status == 'ended':
            competition_list = ended_my_competitions[:competition_count] if competition_count else ended_my_competitions

            if not competition_list:
                return Response({'detail':'종료한 대회가 없습니다.'}, status=status.HTTP_200_OK)

        # 참가 신청하지 않은 대회
        elif competition_status == 'noapply':
            competition_list = not_apply_competitions[:competition_count] if competition_count else not_apply_competitions
                
            if not competition_list:
                return Response({'detail':'신청 가능한 대회를 불러옵니다.'}, status=status.HTTP_200_OK)
            
        # status 미입력시 신청한 전체 대회 목록
        else:
            from datetime import datetime

            competition_list = before_my_competitions+during_my_competitions+ended_my_competitions
            
            #competition_list는 직렬화된 데이터라 start_date는 문자열인 상태 -> 문자열을 파싱하여 날짜 형식으로 변환한 후 정렬
            competition_list = sorted(competition_list, key=lambda x: datetime.strptime(x['start_date'], '%Y-%m-%dT%H:%M:%S'))

            if not competition_list :
                return Response({'detail':'아직 참가신청한 대회가 없습니다.'}, status=status.HTTP_200_OK)
        

        return Response(competition_list, status=status.HTTP_200_OK)


## 대괴 결과 조회
class CompetitionResultView(APIView):
    """
    대회 결과 조회
    """
    permission_classes = [AllowAny]
    
    @swagger_auto_schema(
        operation_summary='대회 결과(우승, 준우승) 조회',
        operation_description='대회 결과(우승, 준우승) 조회 API',
        responses={
            200: CompetitionResultSerializer(many=True), #딸깍
            404: 'Not Found: 대회를 찾을 수 없습니다.',
            404: 'Not Found: 대회 결과를 찾을 수 없습니다..',
        }
    )
    
    def get(self, request, pk):
        try:
            competition = Competition.objects.get(pk=pk)
            result = CompetitionResult.objects.get(competition=competition)
            serializer = CompetitionResultSerializer(result)
            return Response(serializer.data)
        except Competition.DoesNotExist:
            return Response({'error': '대회를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)
        except CompetitionResult.DoesNotExist:
            return Response({'error': '대회 결과를 찾을 수 없습니다.'}, status=status.HTTP_404_NOT_FOUND)