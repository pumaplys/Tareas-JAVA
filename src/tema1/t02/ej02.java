package tema1.t02;

public class ej02 {
    public static void main(String[] args) {
        int C = 8;

        System.out.println(C + " es " + ((C < 0) ? "Negativo" : "Positivo"));
        System.out.println(C + " es " + ((C % 2 == 0) ? "par" : "impar"));
        System.out.println(C + ((C % 5 == 0) ? " Es multiplo de 5" : " No es multiplido de 5"));
        System.out.println(C + ((C % 10 == 0) ? " Es multiplo de 10" : " No es multiplido de 10"));
        System.out.println(C + ((C < 100) ? " Es menor que 100" : " Es mayor que 100"));
    }
}
